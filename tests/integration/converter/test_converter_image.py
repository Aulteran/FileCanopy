"""The converter container image, built for real and run sealed (M10, ADR-019 section 5): a real
LibreOffice converts documents generated inside the container, with no network, a read-only root
and all capabilities dropped. Runs in CI (job `converter`) and with `npm run test:compose`."""

import json
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.compose
ROOT = Path(__file__).resolve().parents[3]
IMAGE = "filecanopy-converter:test"


def docker(*args: str, check: bool = True, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["docker", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def sealed() -> Iterator[str]:
    docker("build", "-q", "-t", IMAGE, str(ROOT / "converter"))
    name = f"fcconv-{uuid.uuid4().hex[:6]}"
    docker(
        "run", "-d", "--name", name,
        "--network", "none",  # no route anywhere, stricter than the internal Compose network
        "--read-only", "--tmpfs", "/tmp:size=768m,mode=1777,noexec,nosuid,nodev",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--user", "10002:10002", "--memory", "2g", "--pids-limit", "256",
        IMAGE,
    )  # fmt: skip
    try:
        yield name
    finally:
        docker("rm", "-f", name, check=False)


def post(name: str, ext: str, data_path: str, max_pages: int = 50) -> tuple[int, bytes]:
    """POST a file that exists inside the container to its own server (no network needed)."""
    script = (
        "import sys, http.client\n"
        f"body = open({data_path!r}, 'rb').read()\n"
        "c = http.client.HTTPConnection('127.0.0.1', 8090, timeout=120)\n"
        f"c.request('POST', '/convert?ext={ext}&max_pages={max_pages}', body, "
        "{'Content-Length': str(len(body))})\n"
        "r = c.getresponse(); data = r.read()\n"
        "sys.stdout.buffer.write(str(r.status).encode() + b'\\n' + data)\n"
    )
    out = subprocess.run(  # noqa: S603
        ["docker", "exec", name, "python3", "-c", script],  # noqa: S607
        capture_output=True, timeout=300, check=True,
    )  # fmt: skip
    status, _, rest = out.stdout.partition(b"\n")
    return int(status), rest


def test_sealed_container_converts_real_documents(sealed: str) -> None:
    # make real input files with the container's own LibreOffice
    docker("exec", sealed, "sh", "-c", "printf 'Sealed hello\\nsecond\\n' > /tmp/a.txt && "
           "printf 'x,y\\n1,2\\n' > /tmp/b.csv && cd /tmp && "
           "soffice --headless --norestore -env:UserInstallation=file:///tmp/mk "
           "--convert-to docx a.txt >/dev/null 2>&1 && "
           "soffice --headless --norestore -env:UserInstallation=file:///tmp/mk "
           "--convert-to xlsx b.csv >/dev/null 2>&1")  # fmt: skip
    for ext, path, needle in (("docx", "/tmp/a.docx", b"%PDF-"), ("xlsx", "/tmp/b.xlsx", b"%PDF-")):
        status, body = post(sealed, ext, path)
        assert status == 200 and body.startswith(needle), (ext, status, body[:200])
    # a junk file and a forbidden type are refused with codes, and the server stays up
    docker("exec", sealed, "sh", "-c", "head -c 3000 /dev/urandom > /tmp/junk.docx")
    status, body = post(sealed, "docx", "/tmp/junk.docx")
    assert status == 422 and json.loads(body)["error"] == "CONVERT_FAILED"
    status, body = post(sealed, "docm", "/tmp/a.docx")
    assert status == 422 and json.loads(body)["error"] == "UNSUPPORTED"
    status, _ = post(sealed, "docx", "/tmp/a.docx")
    assert status == 200


def test_container_is_actually_sealed(sealed: str) -> None:
    insp = json.loads(docker("inspect", sealed).stdout)[0]
    host = insp["HostConfig"]
    assert host["ReadonlyRootfs"] is True and host["CapDrop"] == ["ALL"]
    assert host["NetworkMode"] == "none" and not insp["Mounts"]
    who = docker("exec", sealed, "id", "-u").stdout.strip()
    assert who == "10002"
    # cannot write outside the scratch tmpfs, cannot reach the network
    wrote = docker("exec", sealed, "sh", "-c", "touch /srv/converter/x", check=False)
    assert wrote.returncode != 0
    net = docker(
        "exec", sealed, "python3", "-c",
        "import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)",
        check=False,
    )  # fmt: skip
    assert net.returncode != 0
    probe = "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8090/healthz').status)"
    health = docker("exec", sealed, "python3", "-c", probe)
    assert health.stdout.strip() == "200"
