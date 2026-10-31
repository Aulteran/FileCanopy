"""Name validation for a single path component (ADR-004 §7). Applied when a node is created and
again every time a path is built, so a corrupted database name cannot become a traversal."""

import unicodedata

MAX_NAME_BYTES = 255


class InvalidName(ValueError):
    """The name is not a safe single path component."""


def validate_name(raw: str) -> str:
    """Returns the NFC-normalized name or raises InvalidName."""
    name = unicodedata.normalize("NFC", raw)
    if not name or name in {".", ".."}:
        raise InvalidName("reserved or empty name")
    if "/" in name or "\\" in name:
        raise InvalidName("separator in name")
    if any(ord(c) < 32 or ord(c) == 127 or unicodedata.category(c) == "Cc" for c in name):
        raise InvalidName("control character in name")
    if len(name.encode("utf-8", errors="strict")) > MAX_NAME_BYTES:
        raise InvalidName("name too long")
    return name
