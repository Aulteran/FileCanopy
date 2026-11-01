"""Client for the optional sealed OCR container (M11, `ocr/`).

The API sends the bytes of a picture or a PDF and gets plain text back. The container has no
network, no library mount and no secrets. Its reply is untrusted: size-capped, decoded as UTF-8
with replacement and stripped of control characters before anything stores it.
"""

import http.client
import json
import re
from typing import BinaryIO

from app.preview import kinds
from app.preview.convert import CHUNK, ERROR_BODY_LIMIT, _connection, configured
from app.preview.sandbox import PreviewError

__all__ = ["configured", "ocr_text", "image_content_ok", "IMAGE_EXT", "OCR_EXT"]

IMAGE_EXT = {"png", "jpg", "jpeg", "tif", "tiff", "webp", "gif"}
OCR_EXT = IMAGE_EXT | {"pdf"}
KNOWN = {"TOO_LARGE", "ENCRYPTED", "OCR_FAILED", "TIMEOUT", "OCR_BUSY", "UNSUPPORTED"}
MAX_REPLY_BYTES = 8 * 1024 * 1024
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def image_content_ok(ext: str, head: bytes) -> bool:
    if ext in {"tif", "tiff"}:
        return head.startswith((b"II*\x00", b"MM\x00*"))
    return kinds.thumb_content_ok(ext, head)


def clean(text: str, max_chars: int) -> str:
    return _CONTROL.sub("", text)[:max_chars]


def ocr_text(
    url: str,
    source: BinaryIO,
    size: int,
    ext: str,
    *,
    timeout: int,
    max_pages: int,
    max_chars: int,
) -> str:
    conn = _connection(url, timeout + 15)
    try:
        try:
            source.seek(0)
            conn.putrequest("POST", f"/ocr?ext={ext}&max_pages={max_pages}")
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
            if sent != size:  # the file changed under us
                raise PreviewError("PREVIEW_FAILED")
            resp = conn.getresponse()
            raw = resp.read(MAX_REPLY_BYTES + 1)
        except PreviewError:
            raise
        except TimeoutError as exc:
            raise PreviewError("TIMEOUT", 504) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise PreviewError("OCR_UNAVAILABLE", 503) from exc
        if resp.status == 200:
            if len(raw) > MAX_REPLY_BYTES:
                raise PreviewError("OCR_FAILED")
            try:
                text = json.loads(raw.decode("utf-8", "replace")).get("text")
            except (ValueError, AttributeError):
                raise PreviewError("OCR_FAILED") from None
            if not isinstance(text, str):
                raise PreviewError("OCR_FAILED")
            return clean(text, max_chars)
        code = _error_code(raw[:ERROR_BODY_LIMIT])
        raise PreviewError(code, {"TIMEOUT": 504, "OCR_BUSY": 503, "TOO_LARGE": 413}.get(code, 422))
    finally:
        conn.close()


def _error_code(raw: bytes) -> str:
    try:
        code = json.loads(raw.decode("utf-8", "replace")).get("error")
    except (ValueError, AttributeError):
        return "OCR_FAILED"
    return code if isinstance(code, str) and code in KNOWN else "OCR_FAILED"
