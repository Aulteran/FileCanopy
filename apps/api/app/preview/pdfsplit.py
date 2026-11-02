"""PDF page descriptors and part extraction (M17). Runs only inside the worker child (see
`sandbox`): pure functions on a seekable binary file that never write a file.

`page_descriptors` reads what the split heuristics and the optional model need (per page: a short
text head, a blank flag, a "Page n of m" marker, the effective rotation). `extract_part` copies
chosen pages into a new PDF and then re-opens the result and checks it against the source (page
count, effective rotation and text fingerprint of every page), so a part that does not match is
never returned. The source file is only ever read.
"""

import hashlib
import io
import re
from typing import Any, BinaryIO

from app.preview.extract import Refuse

MAX_PARTS_PAGES = 500
BLANK_CONTENT_BYTES = 64
_MARKER = re.compile(r"(?i)\bpage\s+(\d{1,4})\s*(?:of|/)\s*(\d{1,4})\b")


def _reader(f: BinaryIO) -> Any:
    from pypdf import PdfReader

    try:
        reader = PdfReader(f, strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise Refuse("ENCRYPTED")
        len(reader.pages)  # force the page tree to parse
    except Refuse:
        raise
    except Exception:  # noqa: BLE001 - hostile or truncated files raise many kinds of errors
        raise Refuse("UNREADABLE") from None
    return reader


def _text(page: Any) -> str:
    try:
        return " ".join((page.extract_text() or "").split())
    except Exception:  # noqa: BLE001 - one bad page does not lose the rest
        return ""


def _has_image(page: Any) -> bool:
    try:
        res = page.get("/Resources")
        res = res.get_object() if res is not None else None
        xo = res.get("/XObject") if res is not None else None
        xo = xo.get_object() if xo is not None else None
        if not xo:
            return False
        return any(xo[k].get_object().get("/Subtype") == "/Image" for k in list(xo)[:50] if xo[k])
    except Exception:  # noqa: BLE001 - unknown structure: treat as "has something"
        return True


def _content_len(page: Any) -> int:
    try:
        data = page.get_contents()
        return len(data.get_data()) if data is not None else 0
    except Exception:  # noqa: BLE001
        return 10**6


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def page_descriptors(f: BinaryIO, max_pages: int, head_chars: int) -> dict[str, Any]:
    reader = _reader(f)
    n = len(reader.pages)
    if n > max_pages:
        raise Refuse("TOO_MANY_PAGES")
    pages: list[dict[str, Any]] = []
    for i in range(n):
        page = reader.pages[i]
        text = _text(page)
        marker = _MARKER.search(text)
        blank = not text and not _has_image(page) and _content_len(page) <= BLANK_CONTENT_BYTES
        try:
            rotation = int(page.rotation) % 360
        except Exception:  # noqa: BLE001
            rotation = 0
        pages.append(
            {
                "n": i + 1,
                "chars": len(text),
                "head": text[:head_chars],
                "blank": blank,
                "has_image": _has_image(page),
                "marker": [int(marker.group(1)), int(marker.group(2))] if marker else None,
                "rotation": rotation,
            }
        )
    try:
        outlines = bool(reader.outline)
    except Exception:  # noqa: BLE001
        outlines = False
    try:
        root = reader.trailer["/Root"]
        names = root.get("/Names")
        attachments = bool(names and names.get_object().get("/EmbeddedFiles"))
    except Exception:  # noqa: BLE001
        attachments = False
    return {"pages": n, "descriptors": pages, "outlines": outlines, "attachments": attachments}


def extract_part(f: BinaryIO, wanted: list[int]) -> bytes:
    """A new PDF made of the given 1-based pages of the source, in the given order, verified
    against the source before it is returned."""
    from pypdf import PdfReader, PdfWriter

    if not wanted or len(wanted) > MAX_PARTS_PAGES:
        raise Refuse("BAD_PAGES")
    reader = _reader(f)
    n = len(reader.pages)
    if any(p < 1 or p > n for p in wanted) or len(set(wanted)) != len(wanted):
        raise Refuse("BAD_PAGES")
    writer = PdfWriter()
    try:
        for p in wanted:
            writer.add_page(reader.pages[p - 1])
        out = io.BytesIO()
        writer.write(out)
    except Exception:  # noqa: BLE001
        raise Refuse("SPLIT_FAILED") from None
    data = out.getvalue()
    check = PdfReader(io.BytesIO(data), strict=False)
    if len(check.pages) != len(wanted):
        raise Refuse("SPLIT_VERIFY_FAILED")
    for i, p in enumerate(wanted):
        src, new = reader.pages[p - 1], check.pages[i]
        if int(src.rotation) % 360 != int(new.rotation) % 360:
            raise Refuse("SPLIT_VERIFY_FAILED")
        if _fingerprint(_text(src)) != _fingerprint(_text(new)):
            raise Refuse("SPLIT_VERIFY_FAILED")
    return data
