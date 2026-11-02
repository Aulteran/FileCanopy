"""Bounded MIME parsing for the inbound mailbox (M13, ADR-007).

The message is untrusted. Limits are checked before and during parsing; the part tree is walked
without recursion; nothing is executed, rendered, decoded beyond a transfer encoding, or written
to disk here. The body text is not kept. Attachment names are display metadata only.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

from app.mail.address import clean_text, single_from

MAX_HEADER_BLOCK = 64 * 1024
MAX_HEADERS = 400
_BOUNDARY = re.compile(rb"boundary=", re.IGNORECASE)
_CTYPE_LINE = re.compile(rb"\ncontent-type:", re.IGNORECASE)
_CT = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/[a-z0-9][a-z0-9!#$&^_.+-]{0,62}$")
_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_BIDI = {"‪", "‫", "‬", "‭", "‮", "⁦", "⁧", "⁨", "⁩"}


class MimeRefused(Exception):
    """The message cannot be taken apart within the limits. `reason` is a stable code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Limits:
    max_bytes: int
    max_parts: int
    max_depth: int
    max_attachments: int


@dataclass
class ParsedAttachment:
    position: int
    display_name: str
    content_type: str
    data: bytes
    sha256: str

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass
class ParsedMessage:
    from_address: str | None
    subject: str
    message_id_hash: str | None
    auth_results: list[str]
    recipient_headers: dict[str, list[str]]
    attachments: list[ParsedAttachment] = field(default_factory=list)


def safe_filename(raw: str | None, position: int, taken: set[str]) -> str:
    """A display name that is one plain path component. Never used as a path on disk here; M14
    validates again when it creates a real file."""
    name = unicodedata.normalize("NFC", raw or "")
    name = "".join(
        c
        for c in name
        if c not in _BIDI and unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn")
    )
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip(" .")
    name = name.lstrip(".") or f"attachment-{position}"
    stem, dot, ext = name.rpartition(".")
    if dot and stem.lower() in _RESERVED or (not dot and name.lower() in _RESERVED):
        name = f"_{name}"
    encoded = name.encode("utf-8")
    if len(encoded) > 200:
        base, dot, ext = name.rpartition(".")
        ext = ext[:16] if dot else ""
        keep = 180 - len(ext)
        stem = (base if dot else name).encode("utf-8")[: max(keep, 1)].decode("utf-8", "ignore")
        name = f"{stem}.{ext}" if ext else stem
    candidate, n = name, 2
    while candidate.lower() in taken:
        base, dot, ext = name.rpartition(".")
        candidate = f"{base} ({n}).{ext}" if dot else f"{name} ({n})"
        n += 1
    taken.add(candidate.lower())
    return candidate


def _content_type(part: EmailMessage) -> str:
    try:
        ct = part.get_content_type().lower()
    except Exception:
        return "application/octet-stream"
    return ct if _CT.match(ct) else "application/octet-stream"


def parse_message(raw: bytes, limits: Limits) -> ParsedMessage:
    if len(raw) > limits.max_bytes:
        raise MimeRefused("TOO_LARGE")
    head_end = min(
        (i for i in (raw.find(b"\r\n\r\n"), raw.find(b"\n\n")) if i >= 0), default=len(raw)
    )
    if head_end > MAX_HEADER_BLOCK or raw.count(b"\n", 0, head_end) > MAX_HEADERS:
        raise MimeRefused("HEADERS_TOO_LARGE")
    # Cheap guard against nesting bombs before the stdlib parser (which recurses) sees them.
    # Counted without copying the whole message (review L4).
    if len(_BOUNDARY.findall(raw)) > limits.max_parts or len(_CTYPE_LINE.findall(raw)) > (
        limits.max_parts * 2
    ):
        raise MimeRefused("TOO_MANY_PARTS")
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
    except RecursionError:
        raise MimeRefused("TOO_DEEP") from None
    except Exception:
        raise MimeRefused("UNPARSEABLE") from None
    if not isinstance(msg, EmailMessage):
        raise MimeRefused("UNPARSEABLE")

    try:
        from_values = [v for k, v in msg.items() if k.lower() == "from"]
        from_address = single_from(list(from_values))
        subject_raw = msg.get("subject")
        subject = clean_text(str(subject_raw), 200) if subject_raw is not None else ""
        mid = msg.get("message-id")
        mid_hash = (
            hashlib.sha256(str(mid).strip().encode("utf-8", "replace")).hexdigest() if mid else None
        )
        auth: list[str] = []
        recipients: dict[str, list[str]] = {}
        for name, value in msg.raw_items():
            lname = name.lower()
            if lname == "authentication-results":
                auth.append(str(value))
            elif lname in ("x-original-to", "delivered-to"):
                recipients.setdefault(lname, []).append(str(value))
    except Exception:
        raise MimeRefused("UNPARSEABLE") from None

    out = ParsedMessage(from_address, subject, mid_hash, auth, recipients)
    taken: set[str] = set()
    stack: list[tuple[EmailMessage, int]] = [(msg, 1)]
    parts = 0
    while stack:
        part, depth = stack.pop()
        parts += 1
        if parts > limits.max_parts:
            raise MimeRefused("TOO_MANY_PARTS")
        if depth > limits.max_depth:
            raise MimeRefused("TOO_DEEP")
        try:
            ctype = _content_type(part)
            if ctype.startswith("multipart/"):
                children = part.get_payload()
                if not isinstance(children, list):
                    raise MimeRefused("UNPARSEABLE")
                stack.extend(
                    (c, depth + 1) for c in reversed(children) if isinstance(c, EmailMessage)
                )
                continue
            is_message = ctype.startswith("message/")
            filename = part.get_filename()
            disposition = (part.get_content_disposition() or "").lower()
            textual = ctype in ("text/plain", "text/html") and not filename
            if textual and disposition != "attachment":
                continue  # the body: not kept
            if is_message:
                payload = part.as_bytes()
            else:
                decoded = part.get_payload(decode=True)
                payload = decoded if isinstance(decoded, bytes) else b""
        except MimeRefused:
            raise
        except Exception:
            raise MimeRefused("UNPARSEABLE") from None
        if len(out.attachments) >= limits.max_attachments:
            raise MimeRefused("TOO_MANY_ATTACHMENTS")
        pos = len(out.attachments) + 1
        out.attachments.append(
            ParsedAttachment(
                pos,
                safe_filename(filename, pos, taken),
                ctype,
                payload,
                hashlib.sha256(payload).hexdigest(),
            )
        )
    out.attachments.sort(key=lambda a: a.position)
    return out
