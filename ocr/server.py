"""The OCR container's tiny HTTP server (standard library only).

    POST /ocr?ext=pdf&max_pages=50     body = the file     ->  200 {"text": "..."}
    GET  /healthz                                          ->  200

One request at a time. In Compose the container has no network at all and listens on a Unix
socket in a volume that only the API shares, a read-only root file system and a tmpfs for scratch
files. Nothing in a reply names the file.
"""

import json
import os
import shutil
import socketserver
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ocr import IMAGE_EXT, OcrError, recognize

MAX_BYTES = int(os.environ.get("OCR_MAX_BYTES", str(64 * 1024 * 1024)))
TIMEOUT = int(os.environ.get("OCR_TIMEOUT_SECONDS", "120"))
SCRATCH = os.environ.get("OCR_SCRATCH") or None
PORT = int(os.environ.get("OCR_PORT", "8091"))
SOCKET = os.environ.get("OCR_SOCKET", "")
CHUNK = 1 << 20
STATUS = {"TOO_LARGE": 413, "TIMEOUT": 504, "UNSUPPORTED": 422, "OCR_FAILED": 422}


class Handler(BaseHTTPRequestHandler):
    server_version = "ocr"
    sys_version = ""

    def _json(self, status: int, payload: dict[str, str]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/healthz":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "NOT_FOUND"})

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if url.path != "/ocr":
            return self._json(404, {"error": "NOT_FOUND"})
        query = parse_qs(url.query)
        ext = (query.get("ext") or [""])[0]
        try:
            max_pages = max(1, min(int((query.get("max_pages") or ["50"])[0]), 500))
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._json(400, {"error": "OCR_FAILED"})
        if ext not in IMAGE_EXT and ext != "pdf":
            return self._json(422, {"error": "UNSUPPORTED"})
        if length <= 0 or length > MAX_BYTES:
            self.close_connection = True
            return self._json(413, {"error": "TOO_LARGE"})
        tmp = Path(tempfile.mkdtemp(prefix="req-", dir=SCRATCH))
        try:
            data = tmp / "in"
            left = length
            with open(data, "wb") as out:
                while left > 0:
                    chunk = self.rfile.read(min(CHUNK, left))
                    if not chunk:
                        return self._json(400, {"error": "OCR_FAILED"})
                    out.write(chunk)
                    left -= len(chunk)
            try:
                text = recognize(
                    data, ext, tmp, timeout=TIMEOUT, max_pages=max_pages, max_bytes=4 * 1024 * 1024
                )
            except OcrError as err:
                return self._json(STATUS.get(err.code, 422), {"error": err.code})
            body = json.dumps({"text": text}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def log_message(self, *_args: object) -> None:  # no access log: it could name files
        return


class UnixServer(socketserver.UnixStreamServer):
    """The same single-threaded request loop, over a Unix socket."""


def make_unix_server(path: str) -> UnixServer:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    server = UnixServer(path, Handler)
    os.chmod(path, 0o666)  # the API runs as another user; the directory is shared with it alone
    return server


def main() -> None:
    if SOCKET:
        make_unix_server(SOCKET).serve_forever()
    else:
        HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
