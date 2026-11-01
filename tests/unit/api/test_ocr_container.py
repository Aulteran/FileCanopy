"""M11 OCR container: its server and runner with fake `tesseract`/`pdftoppm` programs, and the
Compose seal. The real Tesseract runs in tests/integration/ocr (CI job `ocr`)."""

import http.client
import importlib
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def ocr_modules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[object, object]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _script(
        bin_dir / "tesseract",
        '#!/bin/sh\ncase "$1" in *hang*) sleep 60;; *bad*) exit 1;; esac\n'
        'echo "recognized text from $(basename "$1" | cut -c1-2)"\n',
    )
    _script(
        bin_dir / "pdftoppm",
        "#!/bin/sh\nfor n in 1 2 3; do echo x > pg-$n.png; done\n",
    )
    monkeypatch.syspath_prepend(str(ROOT / "ocr"))
    monkeypatch.setenv("OCR_PATH", f"{bin_dir}:/usr/bin:/bin")
    for name in ("ocr", "server"):
        sys.modules.pop(name, None)
    ocr = importlib.import_module("ocr")
    server = importlib.import_module("server")
    monkeypatch.setattr(server, "SCRATCH", str(tmp_path))
    yield ocr, server
    for name in ("ocr", "server"):
        sys.modules.pop(name, None)


def _script(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def serve(server: object) -> tuple[HTTPServer, int]:
    httpd = HTTPServer(("127.0.0.1", 0), server.Handler)  # type: ignore[attr-defined]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_port


def post(port: int, ext: str, body: bytes, pages: int = 50) -> tuple[int, dict[str, str]]:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.request("POST", f"/ocr?ext={ext}&max_pages={pages}", body, {"Content-Length": str(len(body))})
    r = c.getresponse()
    return r.status, json.loads(r.read())


def test_pictures_and_pdfs_become_text(ocr_modules: tuple[object, object]) -> None:
    _, server = ocr_modules
    httpd, port = serve(server)
    try:
        status, out = post(port, "png", b"\x89PNGxx")
        assert status == 200 and out["text"].startswith("recognized text from")
        status, out = post(port, "pdf", b"%PDF-1.4 fake")
        assert status == 200 and out["text"].count("recognized text") == 3  # three pages
        status, out = post(port, "pdf", b"%PDF-1.4 fake", pages=2)
        assert status == 200
        c = http.client.HTTPConnection("127.0.0.1", port)
        c.request("GET", "/healthz")
        assert c.getresponse().status == 200
    finally:
        httpd.shutdown()


def test_refusals_are_coded_and_never_name_the_file(ocr_modules: tuple[object, object]) -> None:
    _, server = ocr_modules
    httpd, port = serve(server)
    try:
        assert post(port, "exe", b"MZ")[1] == {"error": "UNSUPPORTED"}
        assert post(port, "pdf", b"not a pdf at all")[1] == {"error": "OCR_FAILED"}
        server.MAX_BYTES = 10  # type: ignore[attr-defined]
        status, out = post(port, "png", b"x" * 100)
        assert status == 413 and out == {"error": "TOO_LARGE"}
        c = http.client.HTTPConnection("127.0.0.1", port)
        c.request("POST", "/elsewhere", b"x", {"Content-Length": "1"})
        assert c.getresponse().status == 404
    finally:
        httpd.shutdown()


def test_a_hung_program_is_killed_with_everything_it_started(
    ocr_modules: tuple[object, object], tmp_path: Path
) -> None:
    ocr, _ = ocr_modules
    data = tmp_path / "hang.png"
    data.write_bytes(b"x")
    start = __import__("time").monotonic()
    with pytest.raises(ocr.OcrError) as err:  # type: ignore[attr-defined]
        ocr.recognize(data, "png", tmp_path, timeout=3, max_pages=1, max_bytes=100)  # type: ignore[attr-defined]
    assert err.value.code == "TIMEOUT" and __import__("time").monotonic() - start < 10


def test_a_failing_program_is_ocr_failed(
    ocr_modules: tuple[object, object], tmp_path: Path
) -> None:
    ocr, _ = ocr_modules
    data = tmp_path / "bad.png"
    data.write_bytes(b"x")
    with pytest.raises(ocr.OcrError) as err:  # type: ignore[attr-defined]
        ocr.recognize(data, "png", tmp_path, timeout=5, max_pages=1, max_bytes=100)  # type: ignore[attr-defined]
    assert err.value.code == "OCR_FAILED"


def test_compose_ocr_service_is_sealed() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    r = subprocess.run(  # noqa: S603
        [
            "docker",  # noqa: S607
            "compose",
            "-f",
            str(ROOT / "deploy/docker-compose.yml"),
            "--profile",
            "ocr",
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
    svc = cfg["services"]["ocr"]
    assert svc["network_mode"] == "none"
    assert not svc.get("networks") and not svc.get("ports") and "extra_hosts" not in svc
    assert [v["target"] for v in svc["volumes"]] == ["/run/ocr"]
    assert svc["volumes"][0]["source"] == "ocr-socket"
    api_mounts = {v["target"]: v["source"] for v in cfg["services"]["api"]["volumes"]}
    assert api_mounts["/run/ocr"] == "ocr-socket"
    for other in ("web", "postgres", "migrate", "converter"):
        if other in cfg["services"]:
            assert "ocr-socket" not in json.dumps(cfg["services"][other].get("volumes", []))
    assert svc["read_only"] is True and svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert svc["user"] == "10003:10003" and int(svc["mem_limit"]) > 0 and svc["pids_limit"] > 0
