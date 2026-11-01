"""Which files the in-app viewer may show, and how (ADR-019).

The type comes from an allow-listed extension plus a check of the first bytes, never from the
browser or a guessed MIME type. The server picks the `Content-Type` it answers with, so a file
named `a.pdf` that is really HTML is refused, and nothing user-supplied is served as markup.
"""

from dataclasses import dataclass

SNIFF_BYTES = 8192
# The text viewer loads at most this much; the page offers Download for the rest (ADR-019 §8).
TEXT_LIMIT = 2 * 1024 * 1024


@dataclass(frozen=True)
class ViewType:
    kind: str  # pdf | image | text | csv
    mime: str  # served Content-Type (text is always text/plain, never HTML)


_EXTENSIONS: dict[str, ViewType] = {
    "pdf": ViewType("pdf", "application/pdf"),
    "png": ViewType("image", "image/png"),
    "jpg": ViewType("image", "image/jpeg"),
    "jpeg": ViewType("image", "image/jpeg"),
    "gif": ViewType("image", "image/gif"),
    "webp": ViewType("image", "image/webp"),
    "svg": ViewType("image", "image/svg+xml"),  # shown as a picture only (an <img>, never inline)
    "txt": ViewType("text", "text/plain; charset=utf-8"),
    "md": ViewType("text", "text/plain; charset=utf-8"),
    "log": ViewType("text", "text/plain; charset=utf-8"),
    "json": ViewType("text", "text/plain; charset=utf-8"),
    "csv": ViewType("csv", "text/plain; charset=utf-8"),
    "tsv": ViewType("csv", "text/plain; charset=utf-8"),
}


def classify(name: str) -> ViewType | None:
    """The viewer type for a file name, or None when the viewer does not handle it."""
    if "." not in name:
        return None
    return _EXTENSIONS.get(name.rsplit(".", 1)[1].lower())


def matches_content(kind: str, mime: str, head: bytes) -> bool:
    """True when the first bytes fit the type the extension claims."""
    if kind == "pdf":
        return b"%PDF-" in head[:1024]
    if kind == "image":
        if mime == "image/png":
            return head.startswith(b"\x89PNG\r\n\x1a\n")
        if mime == "image/jpeg":
            return head.startswith(b"\xff\xd8\xff")
        if mime == "image/gif":
            return head.startswith((b"GIF87a", b"GIF89a"))
        if mime == "image/webp":
            return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
        if mime == "image/svg+xml":
            return b"<svg" in head.lower() and b"\x00" not in head
        return False
    # text and csv: no NUL bytes; must be valid UTF-8 apart from a cut multi-byte character at the
    # end of the sample.
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start < len(head) - 4:
            return False
    return True


def parse_range(header: str, size: int) -> tuple[int, int] | str | None:
    """Parses a single `bytes=` range into inclusive (start, end).

    Returns None when the header is not a usable single range (the caller then answers with the
    whole file, as RFC 9110 allows) and "unsatisfiable" when it lies wholly beyond the file."""
    unit, _, spec = header.partition("=")
    if unit.strip().lower() != "bytes" or "," in spec:
        return None
    first, dash, last = spec.strip().partition("-")
    # digits only (RFC 9110): int() alone would also accept "+5", " 5" and "1_000"
    if not dash or not all(p == "" or (p.isascii() and p.isdigit()) for p in (first, last)):
        return None
    if first == "" and last == "":
        return None
    try:
        if first == "":  # suffix: the last N bytes
            n = int(last)
            if n <= 0:
                return "unsatisfiable"
            return (max(size - n, 0), size - 1) if size > 0 else "unsatisfiable"
        start = int(first)
        end = int(last) if last != "" else size - 1
    except ValueError:
        return None
    if start < 0 or (last != "" and end < start):
        return None
    if start >= size:
        return "unsatisfiable"
    return start, min(end, size - 1)
