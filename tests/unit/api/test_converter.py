"""M10 converter container (ADR-019 section 5): the conversion wrapper, its HTTP server and the API
client, with a fake `soffice`; plus real LibreOffice checks where it is installed."""

import importlib.util
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import HTTPServer
from pathlib import Path
from types import ModuleType

import pytest
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "integration" / "converter"))

from macrofixture import make_macro_odt  # noqa: E402

from app.preview import convert as client  # noqa: E402
from app.preview.sandbox import PreviewError

ROOT = Path(__file__).resolve().parents[3]


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"conv_{name}", ROOT / "converter" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"conv_{name}"] = mod
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


lo = load("convert")
srv = load("server")

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def fake_soffice(tmp_path: Path, body: str) -> str:
    """A stand-in for soffice: `body` runs with `src` (the input path) and `outdir` known."""
    script = tmp_path / f"soffice-{uuid.uuid4().hex[:6]}"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys, os, time\n"
        "args = sys.argv[1:]\n"
        "outdir = args[args.index('--outdir') + 1]\n"
        "src = args[-1]\n"
        f"{body}\n"
    )
    script.chmod(0o755)
    return str(script)


def run_convert(tmp_path: Path, soffice: str, **kw: object) -> Path:
    data = tmp_path / "in.docx"
    data.write_bytes(b"PK\x03\x04 pretend")
    out = tmp_path / "out.pdf"
    lo.convert(data, "docx", out, **{"timeout": 5, "max_pages": 10, "soffice": soffice, **kw})
    return out


def test_profile_pins_macro_security_and_active_content_off() -> None:
    reg = lo.REGISTRY
    assert '"MacroSecurityLevel" oor:op="fuse"><value>3</value>' in reg
    assert '"DisableActiveContent" oor:op="fuse"><value>true</value>' in reg
    assert "Update" in reg and "<value>2</value>" in reg  # link updates: never


def test_conversion_uses_a_fresh_profile_options_and_cleans_up(tmp_path: Path) -> None:
    log = tmp_path / "argv.txt"
    body = (
        f"open({str(log)!r}, 'a').write(repr(args) + chr(10))\n"
        "prof = [a for a in args if a.startswith('-env:UserInstallation=')][0][len('-env:UserInstallation=file://'):]\n"
        "reg = open(prof + '/user/registrymodifications.xcu').read()\n"
        f"open({str(log)!r}, 'a').write('MACRO3' if '<value>3</value>' in reg else 'WEAK')\n"
        "open(outdir + '/input.pdf', 'wb').write(b'%PDF-1.4 ok')\n"
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    out = run_convert(tmp_path, fake_soffice(tmp_path, body), scratch=str(scratch))
    assert out.read_bytes().startswith(b"%PDF-")
    text = log.read_text()
    assert "--headless" in text and "--norestore" in text and "MACRO3" in text
    assert '"value":"1-11"' in text  # page range: one over the limit of 10
    assert "writer_pdf_Export" in text
    assert list(scratch.iterdir()) == []  # the work directory is gone


def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    marker = f"lingering-{uuid.uuid4().hex}"
    body = (
        "import subprocess\n"
        f"subprocess.Popen([sys.executable, '-c', 'import time\\n# {marker}\\ntime.sleep(120)'])\n"
        "time.sleep(120)\n"
    )
    start = time.monotonic()
    with pytest.raises(lo.ConvertError) as info:
        run_convert(tmp_path, fake_soffice(tmp_path, body), timeout=1)
    assert info.value.code == "TIMEOUT" and time.monotonic() - start < 10
    time.sleep(0.3)
    found = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)  # noqa: S603, S607
    assert found.stdout.strip() == ""  # the grandchild did not survive


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("sys.exit(1)", "CONVERT_FAILED"),
        ("sys.exit(0)", "CONVERT_FAILED"),  # no output file
        ("open(outdir + '/input.pdf', 'wb').write(b'<html>not a pdf</html>')", "CONVERT_FAILED"),
        ("open(outdir + '/input.pdf', 'wb').write(b'')", "CONVERT_FAILED"),
    ],
)
def test_bad_outcomes_are_coded(tmp_path: Path, body: str, code: str) -> None:
    with pytest.raises(lo.ConvertError) as info:
        run_convert(tmp_path, fake_soffice(tmp_path, body))
    assert info.value.code == code
    assert not (tmp_path / "out.pdf").exists()


def test_unknown_extension_is_unsupported(tmp_path: Path) -> None:
    data = tmp_path / "x"
    data.write_bytes(b"x")
    for ext in ("docm", "xlsm", "html", "rtf", "exe", ""):
        with pytest.raises(lo.ConvertError) as info:
            lo.convert(data, ext, tmp_path / "o.pdf", timeout=1, max_pages=1)
        assert info.value.code == "UNSUPPORTED"


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    ok = fake_soffice(tmp_path, f"open(outdir + '/input.pdf', 'wb').write({PDF!r})")
    monkeypatch.setattr(srv, "SOFFICE", ok)
    monkeypatch.setattr(srv, "SCRATCH", str(tmp_path))
    monkeypatch.setattr(srv, "MAX_BYTES", 5000)
    monkeypatch.setattr(srv, "TIMEOUT", 5)
    httpd = HTTPServer(("127.0.0.1", 0), srv.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


def test_server_end_to_end_with_the_api_client(server: str, tmp_path: Path) -> None:
    src = tmp_path / "doc.docx"
    src.write_bytes(b"PK\x03\x04" + b"x" * 100)
    dest = tmp_path / "result.pdf"
    with open(src, "rb") as f:
        client.convert_to_pdf(server, f, 104, "docx", dest, timeout=5, max_pages=10)
    assert dest.read_bytes() == PDF
    # the request leaves nothing behind on the converter's scratch disk (it cleans up just after
    # sending the answer, so give it a moment)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        left = [p for p in tmp_path.iterdir() if p.name.startswith(("req-", "conv-"))]
        if not left:
            break
        time.sleep(0.05)
    assert not left


def test_api_client_reaches_the_converter_over_a_unix_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok = fake_soffice(tmp_path, f"open(outdir + '/input.pdf', 'wb').write({PDF!r})")
    monkeypatch.setattr(srv, "SOFFICE", ok)
    monkeypatch.setattr(srv, "SCRATCH", str(tmp_path))
    sock = tmp_path / "c.sock"
    httpd = srv.make_unix_server(str(sock))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        assert oct(sock.stat().st_mode & 0o777) == "0o666"
        src = tmp_path / "d.docx"
        src.write_bytes(b"PK\x03\x04" + b"x" * 50)
        with open(src, "rb") as f:
            client.convert_to_pdf(
                f"unix://{sock}", f, 54, "docx", tmp_path / "o.pdf", timeout=5, max_pages=5
            )
        assert (tmp_path / "o.pdf").read_bytes() == PDF
        with open(src, "rb") as f, pytest.raises(PreviewError) as down:
            client.convert_to_pdf(
                "unix:///nonexistent/c.sock", f, 54, "docx", tmp_path / "o2", timeout=2, max_pages=5
            )
        assert down.value.code == "CONVERTER_UNAVAILABLE"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_server_refusals_map_to_api_codes(server: str, tmp_path: Path) -> None:
    src = tmp_path / "d"
    src.write_bytes(b"x" * 6000)
    with open(src, "rb") as f, pytest.raises(PreviewError) as info:
        client.convert_to_pdf(server, f, 6000, "docx", tmp_path / "o", timeout=5, max_pages=5)
    assert info.value.code == "TOO_LARGE" and info.value.status == 413
    with open(src, "rb") as f, pytest.raises(PreviewError) as info:
        client.convert_to_pdf(server, f, 100, "docm", tmp_path / "o2", timeout=5, max_pages=5)
    assert info.value.code == "UNSUPPORTED"
    import urllib.request

    assert urllib.request.urlopen(server + "/healthz", timeout=3).status == 200  # noqa: S310
    with pytest.raises(PreviewError) as down:
        with open(src, "rb") as f:
            client.convert_to_pdf(
                "http://127.0.0.1:9", f, 100, "docx", tmp_path / "o3", timeout=2, max_pages=5
            )
    assert down.value.code == "CONVERTER_UNAVAILABLE"
    with pytest.raises(PreviewError) as bad:
        with open(src, "rb") as f:
            client.convert_to_pdf(
                "https://x", f, 100, "docx", tmp_path / "o4", timeout=2, max_pages=5
            )
    assert bad.value.code == "CONVERTER_UNAVAILABLE"  # only plain http on the internal network


# ------------------------------------------------------------ real LibreOffice, where installed

real = pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")


def make_real(tmp_path: Path, name: str, content: str, to: str) -> Path:
    base = tmp_path / "mk"
    base.mkdir(exist_ok=True)
    src = base / name
    src.write_text(content)
    profile = tmp_path / f"prof-{uuid.uuid4().hex[:6]}"
    subprocess.run(  # noqa: S603
        [
            "soffice",
            "--headless",
            "--norestore",
            f"-env:UserInstallation=file://{profile}",  # noqa: S607
            "--convert-to",
            to,
            "--outdir",
            str(base / "out"),
            str(src),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return next((base / "out").glob(f"*.{to}"))


@real
def test_real_libreoffice_converts_word_and_excel(tmp_path: Path) -> None:
    docx = make_real(tmp_path, "letter.txt", "Hello sealed converter\nSecond line\n", "docx")
    xlsx = make_real(tmp_path, "sheet.csv", "a,b\n1,2\n", "xlsx")
    for path, ext, needle in ((docx, "docx", "Hello sealed converter"), (xlsx, "xlsx", "a")):
        out = tmp_path / f"{ext}.pdf"
        lo.convert(path, ext, out, timeout=90, max_pages=50)
        assert needle in (PdfReader(str(out)).pages[0].extract_text() or "")


@real
def test_real_libreoffice_converts_opendocument_with_the_pinned_profile(tmp_path: Path) -> None:
    # Macro non-execution is enforced by the profile (see the registry test above). A headless
    # conversion did not fire an on-load Basic macro even with security at "low" in a control run,
    # so no end-to-end macro test is claimed here (documented in M10-design-and-plan.md).
    odt = make_real(tmp_path, "note.txt", "Open document text\n", "odt")
    out = tmp_path / "odt.pdf"
    lo.convert(odt, "odt", out, timeout=90, max_pages=5)
    assert "Open document text" in (PdfReader(str(out)).pages[0].extract_text() or "")


# ------------------------------------------------------------ the Compose seal


def test_compose_converter_service_is_sealed() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    import json

    r = subprocess.run(  # noqa: S603
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "deploy/docker-compose.yml"),  # noqa: S607
            "--profile",
            "converter",
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "POSTGRES_PASSWORD": "x"},
        timeout=60,
    )
    if r.returncode != 0:
        pytest.skip("docker compose not usable here")
    cfg = json.loads(r.stdout)
    svc = cfg["services"]["converter"]
    assert svc["network_mode"] == "none"  # no network at all: not even Docker's DNS
    assert not svc.get("networks") and not svc.get("ports") and "extra_hosts" not in svc
    assert "converter-net" not in cfg.get("networks", {})
    # its only volume holds the socket and nothing else, and the API shares exactly that one
    assert [v["target"] for v in svc["volumes"]] == ["/run/converter"]
    assert svc["volumes"][0]["source"] == "converter-socket"
    api_mounts = {v["target"]: v["source"] for v in cfg["services"]["api"]["volumes"]}
    assert api_mounts["/run/converter"] == "converter-socket"
    for other in ("web", "postgres", "migrate"):
        assert "converter-socket" not in json.dumps(cfg["services"][other].get("volumes", []))
    assert svc["read_only"] is True and svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert svc["user"] == "10002:10002" and int(svc["mem_limit"]) > 0 and svc["pids_limit"] > 0
    assert any("noexec" in str(t) for t in svc["tmpfs"])
    assert not svc.get("privileged")
    # off by default: without the profile there is no converter service
    plain = subprocess.run(  # noqa: S603
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "deploy/docker-compose.yml"),
            "config",
            "--format",
            "json",
        ],  # noqa: S607
        capture_output=True,
        text=True,
        env={**os.environ, "POSTGRES_PASSWORD": "x"},
        timeout=60,
    )
    assert "converter" not in json.loads(plain.stdout)["services"]


@real
def test_real_libreoffice_converts_a_document_with_an_autorun_macro_without_running_it(
    tmp_path: Path,
) -> None:
    """The fixture has a Basic macro bound to document load that writes a marker file. With the
    converter's pinned profile (and with `--convert-to` itself) it must not run. Honest note: a
    control run with macro security set to "low" did not run it either, so headless conversion
    appears never to run document macros; the API additionally refuses packages that contain
    macros (see test_previews)."""
    plain = make_real(tmp_path, "note.txt", "document with a macro\n", "odt")
    marker = tmp_path / "MARKER"
    macro = tmp_path / "macro.odt"
    macro.write_bytes(make_macro_odt(plain.read_bytes(), str(marker)))
    out = tmp_path / "macro.pdf"
    lo.convert(macro, "odt", out, timeout=90, max_pages=5)
    assert "document with a macro" in (PdfReader(str(out)).pages[0].extract_text() or "")
    assert not marker.exists()
