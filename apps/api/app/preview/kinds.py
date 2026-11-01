"""Which files each derivative can be made from. Extension allow-list plus a content check; the
browser's and the file system's idea of a type is never trusted."""

from dataclasses import dataclass

# Word, Excel, PowerPoint and their OpenDocument cousins. Macro-enabled variants (docm, xlsm,
# pptm) and templates are deliberately absent: they are refused, not converted.
OOXML = {"docx", "xlsx", "pptx"}
ODF = {"odt", "ods", "odp"}
LEGACY = {"doc", "xls", "ppt"}
OFFICE = OOXML | ODF | LEGACY
THUMB_MIMES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}
EXTRACT = {"pdf", "docx"}
ZIP_EXT = {"zip"}

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06")


@dataclass(frozen=True)
class Source:
    ext: str


def ext_of(name: str) -> str | None:
    if "." not in name:
        return None
    return name.rsplit(".", 1)[1].lower()


def is_office(ext: str | None) -> bool:
    return ext in OFFICE


def office_content_ok(ext: str, head: bytes) -> bool:
    if ext in LEGACY:
        return head.startswith(OLE_MAGIC)
    return head.startswith(ZIP_MAGICS)


def thumb_content_ok(ext: str, head: bytes) -> bool:
    mime = THUMB_MIMES.get(ext)
    if mime == "image/png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if mime == "image/gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if mime == "image/webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return False


def extract_content_ok(ext: str, head: bytes) -> bool:
    if ext == "pdf":
        return b"%PDF-" in head[:1024]
    if ext == "docx":
        return head.startswith(ZIP_MAGICS) or head.startswith(OLE_MAGIC)
    return False


def zip_content_ok(head: bytes) -> bool:
    return head.startswith(ZIP_MAGICS)
