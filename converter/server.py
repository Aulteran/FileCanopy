"""The converter's tiny HTTP server (standard library only).

    POST /convert?ext=docx&max_pages=500     body = the file     ->  200 application/pdf
    GET  /healthz                                                ->  200

One conversion at a time (a single-threaded server: others wait in the socket queue). The API is
the only client; the container has no network beyond the internal Compose network it shares with
the API, no volumes and a read-only root file system. Nothing in a reply names the file.
"""

import json
import os
import shutil
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from convert import FILTERS, ConvertError, convert

MAX_BYTES = int(os.environ.get("CONVERTER_MAX_BYTES", str(64 * 1024 * 1024)))
TIMEOUT = int(os.environ.get("CONVERTER_TIMEOUT_SECONDS", "60"))
SOFFICE = os.environ.get("CONVERTER_SOFFICE", "soffice")
SCRATCH = os.environ.get("CONVERTER_SCRATCH") or None
PORT = int(os.environ.get("CONVERTER_PORT", "8090"))
CHUNK = 1 << 20
STATUS = {"TOO_LARGE": 413, "TIMEOUT": 504, "UNSUPPORTED": 422, "CONVERT_FAILED": 422}


class Handler(BaseHTTPRequestHandler):
    server_version = "converter"
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
        if url.path != "/convert":
            return self._json(404, {"error": "NOT_FOUND"})
        query = parse_qs(url.query)
        ext = (query.get("ext") or [""])[0]
        try:
            max_pages = max(1, min(int((query.get("max_pages") or ["500"])[0]), 5000))
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._json(400, {"error": "CONVERT_FAILED"})
        if ext not in FILTERS:
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
                        return self._json(400, {"error": "CONVERT_FAILED"})
                    out.write(chunk)
                    left -= len(chunk)
            pdf = tmp / "out.pdf"
            try:
                convert(
                    data, ext, pdf, timeout=TIMEOUT, max_pages=max_pages, soffice=SOFFICE,
                    scratch=SCRATCH,
                )
            except ConvertError as err:
                return self._json(STATUS.get(err.code, 422), {"error": err.code})
            size = pdf.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(pdf, "rb") as f:
                while chunk := f.read(CHUNK):
                    self.wfile.write(chunk)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def log_message(self, *_args: object) -> None:  # no access log: it could name files
        return


def main() -> None:
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104 - internal network only


if __name__ == "__main__":
    main()
