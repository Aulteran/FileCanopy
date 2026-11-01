"""M10 previews and safe extraction (ADR-019): text extraction, thumbnails, ZIP inspection, the
Office converter client, the local-processing opt-in, derivative lifetime and the child-process
sandbox."""

import hashlib
import io
import json
import os
import sys
import threading
import uuid
import zipfile
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pypdf import PdfWriter
from test_acl import Fx
from test_auth import PASSWORD, World
from test_files import Api, code, nid
from test_privacy import label, unlock
from test_trash import events_of

from app.preview import kinds
from app.preview.cache import PreviewCache, make_key
from app.preview.sandbox import Limits, PreviewError, run_child

# ---------------------------------------------------------------- fixtures and builders


def make_pdf(text: str = "Hello FileCanopy") -> bytes:
    stream = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def blank_pdf(pages: int) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(100, 100)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def encrypted_pdf() -> bytes:
    w = PdfWriter()
    w.add_blank_page(100, 100)
    w.encrypt("hunter2")
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def make_docx(paragraphs: list[str], extra: dict[str, bytes] | None = None) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def make_png(size: tuple[int, int] = (64, 48)) -> bytes:
    img = Image.new("RGB", size, (200, 30, 30))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def make_jpeg_with_exif() -> bytes:
    img = Image.new("RGB", (800, 600), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"  # Make
    exif[0x8825] = {1: "N", 2: (51.0, 30.0, 0.0)}  # GPS block
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


class FakeConverter:
    """Stands in for the sealed container: counts requests and answers per `mode`."""

    def __init__(self) -> None:
        self.hits = 0
        self.mode = "ok"
        self.pdf = blank_pdf(1)
        self.last_query = ""
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                n = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(n)
                outer.hits += 1
                outer.last_query = self.path
                if outer.mode == "ok":
                    self._send(200, outer.pdf, "application/pdf")
                elif outer.mode == "garbage":
                    self._send(200, b"this is not a pdf", "application/pdf")
                else:
                    self._send(422, json.dumps({"error": outer.mode}).encode(), "application/json")

            def _send(self, status: int, body: bytes, ctype: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def converter() -> Iterator[FakeConverter]:
    c = FakeConverter()
    yield c
    c.close()


@pytest.fixture
def make_fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Fx]:
    def factory(**overrides: Any) -> Fx:
        return Fx(World(tmp_path, monkeypatch, **overrides))

    return factory


@pytest.fixture
def fx(make_fx: Callable[..., Fx]) -> Fx:
    return make_fx()


@pytest.fixture
def alice(fx: Fx) -> Api:
    return Api(fx, "alice")


def get(api: Api, node: uuid.UUID, what: str, **kw: Any) -> Any:
    return api.get(f"/nodes/{node}/{what}", **kw)


def put_optin(api: Api, enabled: bool) -> Any:
    return api.client.put(
        "/api/v1/preferences/local-processing", json={"enabled": enabled}, headers=api.headers
    )


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def library_hashes(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}


def previews_dir(fx: Fx) -> Path:
    return fx.w.settings.app_data_path / "previews"


# ---------------------------------------------------------------- text extraction


def processes_with(marker: str) -> list[str]:
    """Command lines (from /proc, so no pgrep is needed) that contain `marker`."""
    found = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            except OSError:
                continue
            if marker in cmd:
                found.append(cmd)
    return found


def test_extract_text_from_pdf_and_docx_and_cache_it(fx: Fx, alice: Api) -> None:
    pdf = nid(alice.upload(fx.alice_root, "report.pdf", make_pdf("Quarterly numbers")))
    docx = nid(alice.upload(fx.alice_root, "letter.docx", make_docx(["Dear Bob", "Pay up"])))
    r = get(alice, pdf, "extract")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "pdf" and body["pages"] == 1 and "Quarterly numbers" in body["text"]
    assert body["truncated"] is False
    r = get(alice, docx, "extract")
    assert r.status_code == 200
    assert r.json()["kind"] == "docx" and r.json()["text"].splitlines() == ["Dear Bob", "Pay up"]
    # stored outside the library, one directory per node, nothing inside the library tree
    assert any(previews_dir(fx).glob(f"{pdf}/*.json"))
    assert not list(fx.w.settings.library_path.rglob("*.json"))
    # the second call is served from the cache: it works even when no worker can start
    again = get(alice, pdf, "extract")
    assert again.json() == body


def test_extract_failures_are_friendly_and_never_touch_the_original(
    fx: Fx, alice: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = fx.w.settings.library_path
    cases = {
        "truncated.pdf": make_pdf()[:120],
        "garbage.pdf": b"%PDF-1.7 this is not really a pdf at all" * 5,
        "locked.pdf": encrypted_pdf(),
        "broken.docx": b"PK\x03\x04" + b"\x00" * 40,
        "doctype.docx": make_docx(
            ["x"], {"word/document.xml": b'<!DOCTYPE d [<!ENTITY a "aaa">]><w:document/>'}
        ),
        "bomb.docx": make_docx(["x"], {"word/document.xml": b"<w:p>" + b"\x00" * (200 << 20)}),
    }
    ids = {name: nid(alice.upload(fx.alice_root, name, data)) for name, data in cases.items()}
    before = library_hashes(lib)
    expect = {
        "truncated.pdf": "UNREADABLE",
        "garbage.pdf": "UNREADABLE",
        "locked.pdf": "ENCRYPTED",
        "broken.docx": "UNREADABLE",
        "doctype.docx": "UNREADABLE",
        "bomb.docx": "TOO_LARGE",
    }
    for name, want in expect.items():
        r = get(alice, ids[name], "extract")
        assert r.status_code in (413, 422) and code(r) == want, (name, r.text)
    assert library_hashes(lib) == before  # originals are byte-identical after every failure
    # a pdf that is really text, and a docx that is not a zip, are refused by the content check
    fake = nid(alice.upload(fx.alice_root, "fake.pdf", b"<html>hi</html>"))
    assert code(get(alice, fake, "extract")) == "TYPE_MISMATCH"


def test_extract_limits_the_text_returned(fx: Fx, alice: Api) -> None:
    big = nid(alice.upload(fx.alice_root, "big.docx", make_docx(["word " * 100] * 3000)))
    body = get(alice, big, "extract").json()
    assert body["chars"] == 200_000 and body["truncated"] is True and len(body["text"]) == 200_000


# ---------------------------------------------------------------- access


def test_derivative_routes_use_the_download_access_rules(fx: Fx, alice: Api) -> None:
    pdf = nid(alice.upload(fx.alice_root, "p.pdf", make_pdf()))
    png = nid(alice.upload(fx.alice_root, "p.png", make_png()))
    zp = nid(alice.upload(fx.alice_root, "p.zip", make_zip({"a.txt": b"a"})))
    doc = nid(alice.upload(fx.alice_root, "p.docx", make_docx(["hi"])))
    routes = [(pdf, "extract"), (png, "thumbnail"), (zp, "archive"), (doc, "preview")]
    assert get(alice, pdf, "extract").status_code == 200
    dave = Api(fx, "dave")
    c, _ = fx.admin
    for node, what in routes:
        for r in (get(dave, node, what), c.get(f"/api/v1/nodes/{node}/{what}")):
            assert r.status_code == 403 and r.json() == {"detail": {"code": "FORBIDDEN"}}
        assert fx.w.browser().get(f"/api/v1/nodes/{node}/{what}").status_code == 401
    # a viewer grant reads derivatives exactly like the file
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    shared = nid(alice.upload(fx.taxes, "t.pdf", make_pdf("shared")))
    assert "shared" in get(Api(fx, "bob"), shared, "extract").json()["text"]


def test_wrong_kind_folders_and_forged_types_are_refused(fx: Fx, alice: Api) -> None:
    txt = nid(alice.upload(fx.alice_root, "n.txt", b"hello"))
    macro = nid(alice.upload(fx.alice_root, "m.docm", make_docx(["x"])))
    fake_png = nid(alice.upload(fx.alice_root, "f.png", make_pdf()))
    fake_zip = nid(alice.upload(fx.alice_root, "f.zip", b"just text"))
    assert code(get(alice, txt, "extract")) == "NOT_PREVIEWABLE"
    assert code(get(alice, txt, "thumbnail")) == "NOT_PREVIEWABLE"
    assert code(get(alice, macro, "preview")) == "NOT_PREVIEWABLE"  # macro-enabled: refused
    assert code(get(alice, fake_png, "thumbnail")) == "TYPE_MISMATCH"
    assert code(get(alice, fake_zip, "archive")) == "TYPE_MISMATCH"
    assert get(alice, fx.taxes, "extract").status_code == 415


# ---------------------------------------------------------------- thumbnails


def test_thumbnail_is_a_reencoded_jpeg_without_metadata(fx: Fx, alice: Api) -> None:
    photo = make_jpeg_with_exif()
    assert b"SecretCameraMaker" in photo
    jid = nid(alice.upload(fx.alice_root, "holiday.jpg", photo))
    r = get(alice, jid, "thumbnail")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert "no-store" in r.headers["cache-control"]
    thumb = Image.open(io.BytesIO(r.content))
    assert thumb.format == "JPEG" and max(thumb.size) <= 320
    assert len(thumb.getexif()) == 0 and b"SecretCameraMaker" not in r.content
    assert b"Exif" not in r.content[:64]
    png = nid(alice.upload(fx.alice_root, "x.png", make_png()))
    assert get(alice, png, "thumbnail").status_code == 200
    # cached: second request is served from previews/
    assert any(previews_dir(fx).glob(f"{jid}/*.jpg"))


def test_thumbnail_refuses_svg_bombs_and_broken_images(fx: Fx, alice: Api) -> None:
    svg = nid(alice.upload(fx.alice_root, "s.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>'))
    assert code(get(alice, svg, "thumbnail")) == "NOT_PREVIEWABLE"  # never rasterized
    huge = io.BytesIO()
    Image.new("1", (10000, 10000)).save(huge, "PNG")  # 100 megapixels, a few KB on disk
    bomb = nid(alice.upload(fx.alice_root, "bomb.png", huge.getvalue()))
    r = get(alice, bomb, "thumbnail")
    assert r.status_code in (413, 422) and code(r) == "TOO_LARGE"
    cut = nid(alice.upload(fx.alice_root, "cut.png", make_png((300, 300))[:200]))
    assert code(get(alice, cut, "thumbnail")) == "UNREADABLE"


# ---------------------------------------------------------------- zip inspection


def test_zip_listing_is_bounded_and_inert(fx: Fx, alice: Api) -> None:
    lib = fx.w.settings.library_path
    inner = make_zip({"deep.txt": b"inside"})
    data = make_zip(
        {
            "docs/a.txt": b"aaa",
            "../evil.txt": b"owned",
            "/etc/passwd": b"root",
            "C:\\boot.ini": b"x",
            "nested.zip": inner,
            "weird\u202ename\x07.txt": b"z",
            "bomb.bin": b"\x00" * (5 << 20),
        }
    )
    zid = nid(alice.upload(fx.alice_root, "archive.zip", data))
    before = library_hashes(lib)
    r = get(alice, zid, "archive")
    assert r.status_code == 200, r.text
    body = r.json()
    names = {e["name"]: e for e in body["entries"]}
    assert body["count"] == 7 and body["truncated"] is False
    assert names["../evil.txt"]["unsafe_name"] and names["/etc/passwd"]["unsafe_name"]
    assert names["C:\\boot.ini"]["unsafe_name"] and not names["docs/a.txt"]["unsafe_name"]
    assert "deep.txt" not in names  # nested archives are not opened
    assert not any("\x07" in n or "\u202e" in n for n in names)
    assert body["suspicious_ratio"] is True  # 5 MB of zeros packed to a few KB
    # nothing was extracted anywhere
    assert library_hashes(lib) == before
    assert not (lib.parent / "evil.txt").exists() and not list(lib.rglob("evil.txt"))


def test_zip_listing_caps_entries(fx: Fx, alice: Api) -> None:
    many = make_zip({f"f{i}.txt": b"" for i in range(2500)})
    zid = nid(alice.upload(fx.alice_root, "many.zip", many))
    body = get(alice, zid, "archive").json()
    assert body["count"] == 2500 and len(body["entries"]) == 1000 and body["truncated"] is True
    junk = nid(alice.upload(fx.alice_root, "junk.zip", b"PK\x03\x04" + b"\xff" * 100))
    assert code(get(alice, junk, "archive")) == "UNREADABLE"


# ---------------------------------------------------------------- Office via the converter


def test_office_without_converter_says_download_instead(fx: Fx, alice: Api) -> None:
    doc = nid(alice.upload(fx.alice_root, "a.docx", make_docx(["hi"])))
    r = get(alice, doc, "preview")
    assert r.status_code == 503 and code(r) == "CONVERTER_UNAVAILABLE"
    assert alice.download(doc).status_code == 200  # download is untouched
    assert get(alice, doc, "extract").status_code == 200  # extraction needs no converter
    caps = alice.get("/preferences/local-processing").json()
    assert caps == {"enabled": False, "converter": False}


def test_office_preview_converts_caches_and_supports_range(
    make_fx: Callable[..., Fx], converter: FakeConverter
) -> None:
    fx = make_fx(converter_url=converter.url)
    alice = Api(fx, "alice")
    converter.pdf = make_pdf("converted")
    sheet_xlsx = make_zip({"[Content_Types].xml": "<Types/>", "xl/workbook.xml": "<w/>"})
    for name, data in (
        ("a.docx", make_docx(["hello"])),
        ("b.xlsx", sheet_xlsx),
        ("c.pptx", make_zip({"[Content_Types].xml": "<Types/>"})),
        ("d.odt", make_zip({"mimetype": "application/vnd.oasis.opendocument.text"})),
        ("e.doc", kinds.OLE_MAGIC + b"\x00" * 600),
    ):
        node = nid(alice.upload(fx.alice_root, name, data))
        r = get(alice, node, "preview")
        assert r.status_code == 200, (name, r.text)
        assert r.headers["content-type"] == "application/pdf" and r.content == converter.pdf
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in r.headers["content-security-policy"]
        assert r.headers["content-disposition"].startswith("inline")
    assert converter.hits == 5
    node = nid(alice.upload(fx.alice_root, "again.docx", make_docx(["again"])))
    assert get(alice, node, "preview").status_code == 200
    hits = converter.hits
    r = get(alice, node, "preview", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == converter.pdf[:10]
    assert r.headers["content-range"] == f"bytes 0-9/{len(converter.pdf)}"
    assert get(alice, node, "preview").status_code == 200
    assert converter.hits == hits  # served from previews/, not converted again
    assert "ext=docx" in converter.last_query
    # only one `viewed` line for the open (the Range request adds nothing)
    assert [e.action for e in events_of(fx, node) if e.action == "viewed"] == ["viewed"]


def test_office_preview_is_refreshed_when_the_file_changes(
    make_fx: Callable[..., Fx], converter: FakeConverter
) -> None:
    fx = make_fx(converter_url=converter.url)
    alice = Api(fx, "alice")
    node = nid(alice.upload(fx.alice_root, "a.docx", make_docx(["one"])))
    get(alice, node, "preview")
    assert converter.hits == 1
    path = next(fx.w.settings.library_path.rglob("a.docx"))
    path.write_bytes(make_docx(["two, changed on the host"]))
    os.utime(path, ns=(1_900_000_000_000_000_000, 1_900_000_000_000_000_000))
    assert get(alice, node, "preview").status_code == 200
    assert converter.hits == 2  # a new key, so a new conversion
    assert len(list(previews_dir(fx).glob(f"{node}/*.pdf"))) == 1  # the old version is gone


def test_office_refusals(make_fx: Callable[..., Fx], converter: FakeConverter) -> None:
    fx = make_fx(converter_url=converter.url, preview_office_max_bytes=2000, preview_max_pages=2)
    alice = Api(fx, "alice")
    small = nid(alice.upload(fx.alice_root, "s.docx", make_docx(["x"])))
    bigger = nid(
        alice.upload(
            fx.alice_root, "big.docx", make_docx(["x"], {"word/media/photo.bin": os.urandom(5000)})
        )
    )
    bomb = nid(
        alice.upload(
            fx.alice_root,
            "bomb.xlsx",
            make_zip({"[Content_Types].xml": "<T/>", "xl/sheet.xml": b"\x00" * (8 << 20)}),
        )
    )
    forged = nid(alice.upload(fx.alice_root, "forged.docx", b"MZ not an office file"))
    renamed_docm = nid(
        alice.upload(
            fx.alice_root,
            "renamed.docx",  # really macro-enabled: the extension says nothing
            make_zip({"[Content_Types].xml": "<T/>", "word/vbaProject.bin": b"\x00" * 64}),
        )
    )
    basic_odt = nid(
        alice.upload(
            fx.alice_root,
            "basic.odt",
            make_zip({"mimetype": "x", "Basic/Standard/Module1.xml": "Sub Main\nEnd Sub"}),
        )
    )
    assert code(get(alice, bigger, "preview")) == "TOO_LARGE"
    assert code(get(alice, bomb, "preview")) == "TOO_LARGE"  # zip bomb: refused before conversion
    assert code(get(alice, forged, "preview")) == "TYPE_MISMATCH"
    assert code(get(alice, renamed_docm, "preview")) == "HAS_MACROS"
    assert code(get(alice, basic_odt, "preview")) == "HAS_MACROS"
    assert converter.hits == 0
    converter.pdf = blank_pdf(3)  # more pages than allowed
    assert code(get(alice, small, "preview")) == "TOO_MANY_PAGES"
    converter.pdf = blank_pdf(1)
    for mode, want in (
        ("ENCRYPTED", "ENCRYPTED"),
        ("TIMEOUT", "TIMEOUT"),
        ("weird", "CONVERT_FAILED"),
    ):
        converter.mode = mode
        r = get(alice, small, "preview")
        assert code(r) == want and r.status_code in (422, 504), (mode, r.text)
    converter.mode = "garbage"  # a converter that answers rubbish is not trusted
    assert code(get(alice, small, "preview")) == "CONVERT_FAILED"
    assert not list(previews_dir(fx).glob(f"{small}/*.pdf"))  # nothing bad was cached
    assert not list((previews_dir(fx) / ".tmp").glob("*"))  # and no temp file was left behind


def test_converter_that_is_down_is_a_friendly_refusal(make_fx: Callable[..., Fx]) -> None:
    fx = make_fx(converter_url="http://127.0.0.1:9")  # nothing listens here
    alice = Api(fx, "alice")
    doc = nid(alice.upload(fx.alice_root, "a.docx", make_docx(["hi"])))
    r = get(alice, doc, "preview")
    assert r.status_code == 503 and code(r) == "CONVERTER_UNAVAILABLE"


# ---------------------------------------------------------------- privacy: the opt-in


def test_optin_endpoint(fx: Fx, alice: Api) -> None:
    assert alice.get("/preferences/local-processing").json()["enabled"] is False
    r = alice.client.put("/api/v1/preferences/local-processing", json={"enabled": True})
    assert r.status_code == 403  # CSRF token required
    assert put_optin(alice, True).json()["enabled"] is True
    assert alice.get("/preferences/local-processing").json()["enabled"] is True
    assert put_optin(alice, False).json()["enabled"] is False
    assert fx.w.browser().get("/api/v1/preferences/local-processing").status_code == 401
    bad = alice.client.put(
        "/api/v1/preferences/local-processing",
        json={"enabled": True, "x": 1},
        headers=alice.headers,
    )
    assert bad.status_code == 422


def test_private_files_need_the_optin_for_every_server_side_derivative(
    make_fx: Callable[..., Fx], converter: FakeConverter
) -> None:
    fx = make_fx(converter_url=converter.url)
    alice = Api(fx, "alice")
    assert label(alice, fx.taxes, "private").status_code == 200
    pdf = nid(alice.upload(fx.taxes, "p.pdf", make_pdf("private words")))
    png = nid(alice.upload(fx.taxes, "p.png", make_png()))
    zp = nid(alice.upload(fx.taxes, "p.zip", make_zip({"a": b"a"})))
    doc = nid(alice.upload(fx.taxes, "p.docx", make_docx(["x"])))
    routes = [(pdf, "extract"), (png, "thumbnail"), (zp, "archive"), (doc, "preview")]
    for node, what in routes:
        r = get(alice, node, what)
        assert r.status_code == 403 and code(r) == "LOCAL_PROCESSING_OPT_IN_REQUIRED", what
    assert converter.hits == 0  # nothing left the API
    # showing the PDF the browser renders itself needs no server-side processing (ADR-019 §6)
    assert get(alice, pdf, "view").status_code == 200
    put_optin(alice, True)
    for node, what in routes:
        assert get(alice, node, what).status_code == 200, what
    assert converter.hits == 1
    # withdrawing the opt-in makes the cached derivatives unreachable at once
    put_optin(alice, False)
    for node, what in routes:
        assert code(get(alice, node, what)) == "LOCAL_PROCESSING_OPT_IN_REQUIRED", what
    # ordinary (Normal) files never need it
    normal = nid(alice.upload(fx.alice_root, "n.pdf", make_pdf("plain")))
    assert get(alice, normal, "extract").status_code == 200


def test_both_the_viewer_and_the_owner_must_opt_in(fx: Fx, alice: Api) -> None:
    label(alice, fx.taxes, "private")
    pdf = nid(alice.upload(fx.taxes, "p.pdf", make_pdf("owner private words")))
    fx.grant(fx.alice, fx.taxes, "bob@example.com", "viewer")
    bob = Api(fx, "bob")
    put_optin(bob, True)  # the grantee agrees, the owner has not
    assert code(get(bob, pdf, "extract")) == "LOCAL_PROCESSING_OPT_IN_REQUIRED"
    put_optin(alice, True)
    assert "owner private words" in get(bob, pdf, "extract").json()["text"]
    put_optin(bob, False)
    assert code(get(bob, pdf, "extract")) == "LOCAL_PROCESSING_OPT_IN_REQUIRED"


def test_confidential_needs_the_unlock_and_the_optin(fx: Fx, alice: Api) -> None:
    label(alice, fx.y2026, "confidential")
    assert unlock(alice, fx.y2026, PASSWORD).status_code == 200
    pdf = nid(alice.upload(fx.y2026, "c.pdf", make_pdf("top secret")))
    put_optin(alice, True)
    fx.w.advance(minutes=10)
    r = get(alice, pdf, "extract")  # ten idle minutes: the lock is shut
    assert r.status_code == 403 and code(r) == "LOCKED"
    assert unlock(alice, fx.y2026, PASSWORD).status_code == 200
    assert "top secret" in get(alice, pdf, "extract").json()["text"]
    put_optin(alice, False)
    assert code(get(alice, pdf, "extract")) == "LOCAL_PROCESSING_OPT_IN_REQUIRED"
    put_optin(alice, True)
    assert get(alice, pdf, "extract").status_code == 200
    fx.w.advance(minutes=5, seconds=1)  # the idle lock closes, cached derivative or not
    assert code(get(alice, pdf, "extract")) == "LOCKED"
    other = Api(fx, "alice")  # another browser of the same account is locked too
    put_optin(other, True)
    assert code(get(other, pdf, "extract")) == "LOCKED"


# ---------------------------------------------------------------- derivative lifetime


def test_derivatives_go_with_the_file(fx: Fx, alice: Api) -> None:
    pdf = nid(alice.upload(fx.alice_root, "p.pdf", make_pdf("bye")))
    folder = fx.folder(fx.alice_root, "Box")
    inner = nid(alice.upload(folder, "i.pdf", make_pdf("inner")))
    get(alice, pdf, "extract")
    get(alice, inner, "extract")
    assert (previews_dir(fx) / str(pdf)).is_dir() and (previews_dir(fx) / str(inner)).is_dir()
    assert alice.client.delete(
        f"/api/v1/nodes/{pdf}", params={"expected_revision": 1}, headers=alice.headers
    ).status_code in (200, 204)
    assert not (previews_dir(fx) / str(pdf)).exists()  # trashed: derivatives removed at once
    r = alice.client.delete(
        f"/api/v1/nodes/{folder}", params={"expected_revision": 1}, headers=alice.headers
    )
    assert r.status_code in (200, 204)
    assert not (previews_dir(fx) / str(inner)).exists()  # a folder takes its files' previews along
    assert get(alice, pdf, "extract").status_code == 403  # and the node itself is out of reach


def test_sweep_removes_orphans_stale_versions_and_trims(fx: Fx, alice: Api) -> None:
    keep = nid(alice.upload(fx.alice_root, "k.pdf", make_pdf("keep")))
    get(alice, keep, "extract")
    cache = PreviewCache(fx.w.settings.app_data_path, 10_000_000)
    stale = cache.path(keep, make_key(1, 1, 1), "json")
    stale.write_bytes(b"{}")
    os.utime(stale, (1, 1))  # older than the real one
    orphan = uuid.uuid4()
    cache.put_bytes(orphan, "abc", "pdf", b"%PDF-orphan")
    (cache.base / "not-a-uuid").mkdir()
    (cache.base / "not-a-uuid" / "x").write_bytes(b"x")
    old_tmp = cache.tmp_path()
    old_tmp.write_bytes(b"partial")
    os.utime(old_tmp, (1, 1))
    out = cache.sweep(fx.w.ctx.sessions)
    assert out["nodes"] == 1 and not (cache.base / str(orphan)).exists()
    assert not stale.exists() and len(list((cache.base / str(keep)).glob("*.json"))) == 1
    assert not (cache.base / "not-a-uuid").exists() and not old_tmp.exists()
    # trimming to a size cap removes the least recently used file first
    tiny = PreviewCache(fx.w.settings.app_data_path, 1)
    assert tiny.sweep(fx.w.ctx.sessions)["trimmed"] >= 1
    assert not list(tiny.base.glob("*/*"))


def test_previews_are_not_part_of_the_library(fx: Fx, alice: Api) -> None:
    pdf = nid(alice.upload(fx.alice_root, "p.pdf", make_pdf("x")))
    get(alice, pdf, "extract")
    assert str(previews_dir(fx)).startswith(str(fx.w.settings.app_data_path))
    assert str(fx.w.settings.library_path) not in str(previews_dir(fx))
    mode = (previews_dir(fx) / str(pdf)).stat().st_mode & 0o777
    assert mode == 0o700


# ---------------------------------------------------------------- the child-process sandbox


LIMITS = Limits(timeout=2.0, memory_bytes=256 * 1024**2, max_output=1024)


def test_sandbox_kills_a_child_that_runs_too_long() -> None:
    marker = f"sleeper-{uuid.uuid4().hex}"
    argv = [sys.executable, "-c", f"import time\n# {marker}\ntime.sleep(60)"]
    with pytest.raises(PreviewError) as info:
        run_child(argv, None, LIMITS)
    assert info.value.code == "TIMEOUT" and info.value.status == 504
    assert processes_with(marker) == []  # the whole process group is gone


def test_sandbox_caps_output_and_memory() -> None:
    flood = [
        sys.executable,
        "-c",
        "import sys\nwhile True: sys.stdout.write('x'*4096); sys.stdout.flush()",
    ]
    with pytest.raises(PreviewError) as info:
        run_child(flood, None, LIMITS)
    assert info.value.code == "OUTPUT_TOO_LARGE"
    hog = (
        "import resource\n"
        "resource.setrlimit(resource.RLIMIT_AS, (200*1024*1024,)*2)\n"
        "x = bytearray(900*1024*1024)\n"
        "print('allocated')"
    )
    code_, out = run_child([sys.executable, "-c", hog], None, LIMITS)
    assert code_ != 0 and b"allocated" not in out


def test_sandbox_child_has_no_secrets_in_its_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FILECANOPY_DATABASE_URL", "postgresql://user:hunter2@db/x")
    monkeypatch.setenv("FILECANOPY_SETUP_TOKEN", "t" * 40)
    argv = [sys.executable, "-c", "import os,json;print(json.dumps(sorted(os.environ)))"]
    status, out = run_child(argv, None, LIMITS)
    names = json.loads(out)
    assert status == 0 and not [n for n in names if n.startswith("FILECANOPY")]
    assert b"hunter2" not in out


def test_worker_survives_hostile_input_as_a_coded_error(fx: Fx, alice: Api) -> None:
    # A PDF whose body is a recursion/length trap must end as a code, not as a crashed API.
    loop = (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 1 0 R >>\nendobj\n"
        b"trailer\n<< /Root 1 0 R >>\n"
    )
    node = nid(alice.upload(fx.alice_root, "loop.pdf", loop))
    r = get(alice, node, "extract")
    assert r.status_code in (422, 504) and code(r) in ("UNREADABLE", "PREVIEW_FAILED", "TIMEOUT")
    assert alice.download(node).content == loop  # and the API is still serving
