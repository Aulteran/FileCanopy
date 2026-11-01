"""The OCR container image, built for real and run sealed (M11): real Tesseract reads a picture and
a scanned (image-only) PDF made here, with no network, a read-only root and all capabilities
dropped. Runs in CI (job `ocr`) and with `npm run test:compose`."""

import io
import json
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

pytestmark = pytest.mark.compose
ROOT = Path(__file__).resolve().parents[3]
IMAGE = "filecanopy-ocr:test"


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
    docker("build", "-q", "-t", IMAGE, str(ROOT / "ocr"))
    name = f"fcocr-{uuid.uuid4().hex[:6]}"
    docker(
        "run", "-d", "--name", name,
        "--network", "none",
        "--read-only", "--tmpfs", "/tmp:size=1g,mode=1777,noexec,nosuid,nodev",
        "--tmpfs", "/run/ocr:mode=0777",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--user", "10003:10003", "--memory", "3g", "--pids-limit", "128",
        IMAGE,
    )  # fmt: skip
    try:
        yield name
    finally:
        docker("rm", "-f", name, check=False)


def page(text: str) -> Image.Image:
    img = Image.new("L", (1400, 400), 255)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=110)
    draw.text((40, 120), text, fill=0, font=font)
    return img


def put(name: str, path: str, data: bytes) -> None:
    subprocess.run(  # noqa: S603
        ["docker", "exec", "-i", name, "sh", "-c", f"cat > {path}"],  # noqa: S607
        input=data,
        check=True,
        timeout=60,
    )


def post(name: str, ext: str, path: str) -> tuple[int, bytes]:
    script = (
        "import sys, socket, http.client\n"
        f"body = open({path!r}, 'rb').read()\n"
        "class C(http.client.HTTPConnection):\n"
        "    def connect(self):\n"
        "        self.sock = socket.socket(socket.AF_UNIX)\n"
        "        self.sock.settimeout(200)\n"
        "        self.sock.connect('/run/ocr/ocr.sock')\n"
        "c = C('ocr')\n"
        f"c.request('POST', '/ocr?ext={ext}&max_pages=5', body, "
        "{'Content-Length': str(len(body))})\n"
        "r = c.getresponse(); data = r.read()\n"
        "sys.stdout.buffer.write(str(r.status).encode() + b'\\n' + data)\n"
    )
    out = subprocess.run(  # noqa: S603
        ["docker", "exec", name, "python3", "-c", script],  # noqa: S607
        capture_output=True,
        timeout=300,
        check=True,
    )
    status, _, rest = out.stdout.partition(b"\n")
    return int(status), rest


def test_a_picture_and_a_scanned_pdf_are_read_by_real_tesseract(sealed: str) -> None:
    img = page("Invoice 4711 Rosewood")
    png = io.BytesIO()
    img.save(png, "PNG")
    put(sealed, "/tmp/a.png", png.getvalue())
    status, body = post(sealed, "png", "/tmp/a.png")
    text = json.loads(body)["text"]
    assert status == 200 and "4711" in text and "Rosewood" in text, text
    pdf = io.BytesIO()
    page("Scanned contract 9182").save(pdf, "PDF")  # an image-only PDF: no text layer
    put(sealed, "/tmp/scan.pdf", pdf.getvalue())
    status, body = post(sealed, "pdf", "/tmp/scan.pdf")
    text = json.loads(body)["text"]
    assert status == 200 and "9182" in text and "contract" in text.lower(), text


def test_junk_is_refused_not_crashed_on(sealed: str) -> None:
    put(sealed, "/tmp/junk.png", b"this is not an image" * 50)
    status, body = post(sealed, "png", "/tmp/junk.png")
    assert status == 422 and json.loads(body) == {"error": "OCR_FAILED"}
    put(sealed, "/tmp/junk.pdf", b"nope")
    status, body = post(sealed, "pdf", "/tmp/junk.pdf")
    assert status == 422 and json.loads(body)["error"] == "OCR_FAILED"


def test_the_container_is_sealed(sealed: str) -> None:
    insp = json.loads(docker("inspect", sealed).stdout)[0]
    assert insp["HostConfig"]["NetworkMode"] == "none"
    assert insp["HostConfig"]["ReadonlyRootfs"] is True
    assert docker("exec", sealed, "id", "-u").stdout.strip() == "10003"
    wrote = docker("exec", sealed, "sh", "-c", "touch /srv/ocr/x", check=False)
    assert wrote.returncode != 0
    ran = docker("exec", sealed, "sh", "-c", "cp /bin/true /tmp/t && /tmp/t", check=False)
    assert ran.returncode != 0  # the tmpfs is noexec
    net = docker(
        "exec", sealed, "python3", "-c",
        "import socket; socket.create_connection(('1.1.1.1', 53), 3)",
        check=False,
    )  # fmt: skip
    assert net.returncode != 0
    assert docker("exec", sealed, "python3", "healthcheck.py", check=False).returncode == 0
