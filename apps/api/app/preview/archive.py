"""Bounded, inert ZIP inspection: reads the central directory only. Never extracts, never opens
an entry, never looks inside nested archives. Runs only inside the worker child."""

import re
import zipfile
from typing import Any, BinaryIO

from app.preview.extract import Refuse

NAME_CAP = 255
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ‪-‮⁦-⁩]")
BOMB_RATIO = 500
BOMB_MIN_BYTES = 1024 * 1024


def list_zip(f: BinaryIO, max_entries: int) -> dict[str, Any]:
    try:
        zf = zipfile.ZipFile(f)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError):
        raise Refuse("UNREADABLE") from None
    try:
        infos = zf.infolist()
    except (zipfile.BadZipFile, OSError, ValueError, EOFError):
        raise Refuse("UNREADABLE") from None
    finally:
        zf.close()
    entries: list[dict[str, Any]] = []
    total = 0
    packed = 0
    encrypted = False
    bomb = False
    macros = False
    for info in infos:
        low = info.filename.lower().replace("\\", "/")
        macros = macros or (
            low.endswith("vbaproject.bin")
            or low.startswith(("basic/", "scripts/"))
            or "/macrosheets/" in low
            or low.startswith("macrosheets/")
        )
        total += info.file_size
        packed += info.compress_size
        encrypted = encrypted or bool(info.flag_bits & 0x1)
        if info.compress_size > 0 and info.file_size >= BOMB_MIN_BYTES:
            bomb = bomb or info.file_size / info.compress_size > BOMB_RATIO
        if len(entries) < max_entries:
            raw = info.filename
            parts = raw.replace("\\", "/").split("/")
            unsafe = (
                raw.startswith(("/", "\\")) or ".." in parts or bool(re.match(r"^[A-Za-z]:", raw))
            )
            name = _CONTROL.sub("�", raw)[:NAME_CAP]
            entries.append(
                {
                    "name": name,
                    "size": info.file_size,
                    "packed": info.compress_size,
                    "folder": info.is_dir(),
                    "unsafe_name": unsafe,
                    "encrypted": bool(info.flag_bits & 0x1),
                }
            )
    return {
        "count": len(infos),
        "entries": entries,
        "truncated": len(infos) > max_entries,
        "total_size": total,
        "total_packed": packed,
        "encrypted": encrypted,
        "suspicious_ratio": bomb,
        "macros": macros,
    }
