"""Client for the optional sealed converter container (ADR-019 section 5, `converter/`).

The API sends the file's bytes and gets a PDF back. The converter has no network, no library
mount and no secrets; nothing it sends back is trusted: the PDF is size-capped here and re-checked
by a limited worker before it is cached.
"""

import http.client
import os
import socket
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlparse

from app.preview.sandbox import PreviewError

CHUNK = 1 << 20
ERROR_BODY_LIMIT = 4096
# Codes a converter may report; anything else becomes CONVERT_FAILED.
KNOWN = {"TOO_LARGE", "ENCRYPTED", "CONVERT_FAILED", "TIMEOUT", "PREVIEW_BUSY", "UNSUPPORTED"}
MAX_PDF_BYTES = 256 * 1024 * 1024


class UnixConnection(http.client.HTTPConnection):
    """HTTP over a Unix socket: how the API reaches the network-less converter container."""

    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("converter", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def _connection(url: str, timeout: float) -> http.client.HTTPConnection:
    parsed = urlparse(url)
    if parsed.scheme == "unix" and parsed.path:
        return UnixConnection(parsed.path, timeout)
    if parsed.scheme == "http" and parsed.hostname:  # development and tests
        return http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)
    raise PreviewError("CONVERTER_UNAVAILABLE", 503)


def configured(url: str) -> bool:
    return bool(url.strip())


def convert_to_pdf(
    url: str, source: BinaryIO, size: int, ext: str, dest: Path, *, timeout: int, max_pages: int
) -> None:
    """Posts the file and writes the returned PDF to `dest` (a new temp path)."""
    conn = _connection(url, timeout + 15)
    try:
        try:
            source.seek(0)
            conn.putrequest("POST", f"/convert?ext={ext}&max_pages={max_pages}")
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("Content-Length", str(size))
            conn.endheaders()
            sent = 0
            while sent < size:
                chunk = source.read(min(CHUNK, size - sent))
                if not chunk:
                    break
                conn.send(chunk)
                sent += len(chunk)
            if sent != size:  # the file changed under us; do not hand a short body over
                raise PreviewError("PREVIEW_FAILED")
            resp = conn.getresponse()
        except PreviewError:
            raise
        except TimeoutError as exc:
            raise PreviewError("TIMEOUT", 504) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise PreviewError("CONVERTER_UNAVAILABLE", 503) from exc
        try:
            if resp.status == 200:
                _save(resp, dest)
                return
            raw = resp.read(ERROR_BODY_LIMIT)
        except TimeoutError as exc:
            raise PreviewError("TIMEOUT", 504) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise PreviewError("CONVERTER_UNAVAILABLE", 503) from exc
        code = _error_code(raw)
        status = {"TIMEOUT": 504, "PREVIEW_BUSY": 503, "TOO_LARGE": 413}.get(code, 422)
        raise PreviewError(code, status)
    finally:
        conn.close()


def _error_code(raw: bytes) -> str:
    import json

    try:
        code = json.loads(raw.decode("utf-8", "replace")).get("error")
    except (ValueError, AttributeError):
        return "CONVERT_FAILED"
    return code if isinstance(code, str) and code in KNOWN else "CONVERT_FAILED"


def _save(resp: http.client.HTTPResponse, dest: Path) -> None:
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    written = 0
    with os.fdopen(fd, "wb") as out:
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_PDF_BYTES:
                raise PreviewError("TOO_LARGE", 413)
            out.write(chunk)
    if written == 0:
        raise PreviewError("CONVERT_FAILED")
