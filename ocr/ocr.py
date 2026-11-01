"""Runs Tesseract on one picture or PDF and returns plain text. Standard library only.

PDFs are rendered page by page with `pdftoppm` (poppler), pictures go straight to `tesseract`.
Every external program runs in its own session with CPU, memory and file-size limits and a
wall-clock deadline for the whole request, so a timeout kills everything that was started.
"""

import os
import resource
import signal
import subprocess
import time
from pathlib import Path

IMAGE_EXT = {"png", "jpg", "jpeg", "tif", "tiff", "webp", "gif"}
LANGS = os.environ.get("OCR_LANGS", "eng")
RENDER_DPI = os.environ.get("OCR_DPI", "200")
MAX_TEXT_BYTES = 8 * 1024 * 1024
# Tesseract under one thread keeps CPU use predictable on a small home server.
ENV = {
    "OMP_THREAD_LIMIT": "1",
    "PATH": os.environ.get("OCR_PATH", "/usr/bin:/bin"),
    "LANG": "C.UTF-8",
}


class OcrError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _limits(cpu_seconds: int, mem_bytes: int) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 30, 1 << 30))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _run(argv: list[str], deadline: float, cwd: Path) -> bytes:
    left = deadline - time.monotonic()
    if left <= 1:
        raise OcrError("TIMEOUT")
    proc = subprocess.Popen(  # noqa: S603
        argv,
        cwd=cwd,
        env=ENV,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        preexec_fn=lambda: _limits(int(left) + 5, 3 * 1024**3),  # noqa: PLW1509
    )
    try:
        out, _ = proc.communicate(timeout=left)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise OcrError("TIMEOUT") from None
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # nothing it started outlives it
        except ProcessLookupError:
            pass
    if proc.returncode != 0:
        raise OcrError("OCR_FAILED")
    return out


def _tesseract(image: Path, deadline: float, cwd: Path) -> str:
    out = _run(["tesseract", str(image), "stdout", "-l", LANGS, "--psm", "3"], deadline, cwd)
    return out[:MAX_TEXT_BYTES].decode("utf-8", "replace")


def recognize(
    data: Path, ext: str, work: Path, *, timeout: int, max_pages: int, max_bytes: int
) -> str:
    deadline = time.monotonic() + timeout
    if ext in IMAGE_EXT:
        return _tesseract(data, deadline, work)
    if ext != "pdf":
        raise OcrError("UNSUPPORTED")
    head = data.read_bytes()[:1024]
    if b"%PDF-" not in head:
        raise OcrError("OCR_FAILED")
    try:
        _run(
            ["pdftoppm", "-r", RENDER_DPI, "-l", str(max_pages), "-gray", "-png", str(data), "pg"],
            deadline,
            work,
        )
    except OcrError as err:
        if err.code == "OCR_FAILED" and b"/Encrypt" in data.read_bytes()[-4096:]:
            raise OcrError("ENCRYPTED") from None
        raise
    pages = sorted(work.glob("pg*.png"))
    if not pages:
        raise OcrError("OCR_FAILED")
    parts: list[str] = []
    total = 0
    for page in pages[:max_pages]:
        text = _tesseract(page, deadline, work)
        parts.append(text)
        total += len(text)
        page.unlink()
        if total >= max_bytes:
            break
    return "\n\n".join(parts)[:max_bytes]
