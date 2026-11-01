"""Image thumbnails. Runs only inside the worker child. The output is a fresh JPEG: no EXIF, GPS,
profile or comment data from the original survives, and SVG is never rasterized here."""

import io
from typing import BinaryIO

from app.preview.extract import Refuse

FORMATS = ["PNG", "JPEG", "GIF", "WEBP"]


def make_thumbnail(f: BinaryIO, edge: int, max_pixels: int) -> bytes:
    from PIL import Image, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = max_pixels  # above this Pillow raises DecompressionBombError
    try:
        with Image.open(f, formats=FORMATS) as img:
            if img.width * img.height > max_pixels:
                raise Refuse("TOO_LARGE")
            img.draft("RGB", (edge * 2, edge * 2))  # lets JPEG decode at reduced size
            img.load()
            rgba = img.convert("RGBA")
    except Refuse:
        raise
    except Image.DecompressionBombError:
        raise Refuse("TOO_LARGE") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError, MemoryError):
        raise Refuse("UNREADABLE") from None
    rgba.thumbnail((edge, edge))
    flat = Image.new("RGB", rgba.size, (255, 255, 255))
    flat.paste(rgba, mask=rgba.getchannel("A"))
    out = io.BytesIO()
    flat.save(out, "JPEG", quality=80, optimize=True)  # nothing passed through: no exif/icc
    return out.getvalue()
