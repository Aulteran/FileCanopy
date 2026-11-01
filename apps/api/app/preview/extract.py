"""Text extraction from PDF and DOCX. Runs only inside the worker child (see `sandbox`).

Pure functions on a seekable binary file; they raise `Refuse(CODE)` for every expected problem
and never write anything.
"""

import zipfile
from typing import Any, BinaryIO
from xml.parsers import expat

MAX_PDF_PAGES_READ = 500
MAX_ZIP_ENTRIES = 5000
MAX_XML_BYTES = 64 * 1024 * 1024
MAX_RATIO = 1000


class Refuse(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def pdf_text(f: BinaryIO, max_chars: int) -> dict[str, Any]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(f, strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise Refuse("ENCRYPTED")
        pages = len(reader.pages)
        parts: list[str] = []
        total = 0
        truncated = pages > MAX_PDF_PAGES_READ
        for i in range(min(pages, MAX_PDF_PAGES_READ)):
            try:
                text = reader.pages[i].extract_text() or ""
            except Exception:  # noqa: BLE001 - one bad page does not lose the rest
                text = ""
            parts.append(text)
            total += len(text)
            if total >= max_chars:
                truncated = True
                break
        text = "\n\n".join(parts)
        if len(text) > max_chars:
            text, truncated = text[:max_chars], True
        return {"kind": "pdf", "pages": pages, "text": text, "truncated": truncated}
    except Refuse:
        raise
    except Exception:  # noqa: BLE001 - truncated or hostile files raise many kinds of errors
        raise Refuse("UNREADABLE") from None


def check_zip(zf: zipfile.ZipFile, *, max_entries: int = MAX_ZIP_ENTRIES) -> list[zipfile.ZipInfo]:
    infos = zf.infolist()
    if len(infos) > max_entries:
        raise Refuse("TOO_LARGE")
    for info in infos:
        if info.flag_bits & 0x1:
            raise Refuse("ENCRYPTED")
        if info.compress_size > 0 and info.file_size / info.compress_size > MAX_RATIO:
            if info.file_size > 1024 * 1024:
                raise Refuse("TOO_LARGE")
    return infos


def docx_text(f: BinaryIO, max_chars: int) -> dict[str, Any]:
    from app.preview.kinds import OLE_MAGIC

    head = f.read(8)
    f.seek(0)
    if head.startswith(OLE_MAGIC):
        raise Refuse("ENCRYPTED")  # a password-protected DOCX is an OLE container
    try:
        zf = zipfile.ZipFile(f)
    except (zipfile.BadZipFile, OSError, ValueError):
        raise Refuse("UNREADABLE") from None
    try:
        check_zip(zf)
        try:
            info = zf.getinfo("word/document.xml")
        except KeyError:
            raise Refuse("UNREADABLE") from None
        if info.file_size > MAX_XML_BYTES:
            raise Refuse("TOO_LARGE")
        with zf.open(info) as part:
            data = part.read(MAX_XML_BYTES + 1)
        if len(data) > MAX_XML_BYTES:
            raise Refuse("TOO_LARGE")
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, EOFError):
        raise Refuse("UNREADABLE") from None
    finally:
        zf.close()
    return _docx_xml_text(data, max_chars)


def _docx_xml_text(data: bytes, max_chars: int) -> dict[str, Any]:
    try:
        xml = data.decode("utf-8")  # Word writes UTF-8; anything else is not worth the risk
    except UnicodeDecodeError:
        raise Refuse("UNREADABLE") from None
    lowered = xml.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise Refuse("UNREADABLE")  # no DTDs, so no entity expansion tricks
    chunks: list[str] = []
    state = {"total": 0, "in_t": False, "truncated": False}

    def add(s: str) -> None:
        if state["truncated"]:
            return
        room = max_chars - state["total"]
        if len(s) >= room:
            s, state["truncated"] = s[:room], True
        chunks.append(s)
        state["total"] += len(s)

    def start(name: str, attrs: dict[str, str]) -> None:
        del attrs
        if name == "w:t":
            state["in_t"] = True
        elif name in ("w:tab",):
            add("\t")
        elif name in ("w:br", "w:cr"):
            add("\n")

    def end(name: str) -> None:
        if name == "w:t":
            state["in_t"] = False
        elif name == "w:p":
            add("\n")

    def chars(text: str) -> None:
        if state["in_t"]:
            add(text)

    parser = expat.ParserCreate()
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    try:
        parser.Parse(xml, True)
    except expat.ExpatError:
        raise Refuse("UNREADABLE") from None
    text = "".join(chunks).strip("\n")
    return {"kind": "docx", "pages": None, "text": text, "truncated": state["truncated"]}
