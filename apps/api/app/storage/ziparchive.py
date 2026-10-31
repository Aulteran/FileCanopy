"""Read-only inspection of a staged zip archive (ADR-011 §4). Standard library only.

Nothing here writes to the library. `inspect_zip` reads the central directory (after walking it
record by record, so a huge or lying directory is never loaded) and classifies every entry; the
import engine decides what to extract. `limited_chunks` streams an entry while counting what comes
out, so sizes in the headers are never trusted.
"""

import calendar
import stat
import struct
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

CHUNK = 1024 * 1024
RATIO_FREE_BYTES = 1024 * 1024  # entries up to this size are never judged by ratio
MAX_DIRECTORY_BYTES = 512 * 1024 * 1024
SUPPORTED_METHODS = frozenset(
    {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}
)

# What one central-directory record may plausibly occupy: the fixed header plus name, extra field
# and comment. The whole directory is bounded by `max_entries * RECORD_BUDGET`.
RECORD_BUDGET = 46 + 2048 + 1024 + 256

_EOCD = b"PK\x05\x06"
_CENTRAL = b"PK\x01\x02"
_ZIP64_LOCATOR = b"PK\x06\x07"


class ZipRefused(Exception):
    """The archive cannot be listed safely. `code` is stable and shown to the importer."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EntryLimit(Exception):
    """An entry went past a size or ratio limit while it was being read."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ZipItem:
    index: int  # position in infolist(), used to open exactly this entry later
    filename: str
    is_dir: bool
    size: int
    compress_size: int
    mtime_ms: int | None
    skip: str | None  # reason code when the entry must not be extracted


def _end_record(path: Path) -> tuple[int, int, int]:
    """(entries, central-directory bytes, offset where the directory must end) as the end record
    declares them, read from the file tail only. Raises ZipRefused when there is no end record.
    The directory ends where the (zip64) end record starts, so its start is `end - bytes`, which
    is also how data prepended to the archive is accommodated."""
    size = path.stat().st_size
    with path.open("rb") as fh:
        tail_len = min(size, 65_557 + 22 + 20)
        fh.seek(size - tail_len)
        tail = fh.read()
    at = tail.rfind(_EOCD)
    if at < 0 or len(tail) - at < 22:
        raise ZipRefused("INVALID_ZIP")
    entries, directory = struct.unpack_from("<HI", tail, at + 10)
    end = size - tail_len + at
    locator = b""
    if end >= 20:
        with path.open("rb") as fh:
            fh.seek(end - 20)
            locator = fh.read(20)
    # CPython uses a zip64 locator found right before the end record whatever the end record
    # declares, so the walk must follow the same record (not only when the fields are saturated).
    if locator[:4] == _ZIP64_LOCATOR:
        (offset,) = struct.unpack_from("<Q", locator, 8)
        record_at = end - 20 - 56  # the record sits right before its locator
        with path.open("rb") as fh:
            record = b""
            for candidate in (offset, record_at):
                if 0 <= candidate <= size - 56:
                    fh.seek(candidate)
                    record = fh.read(56)
                    if record[:4] == b"PK\x06\x06":
                        end = candidate
                        break
        if len(record) < 56 or record[:4] != b"PK\x06\x06":
            raise ZipRefused("INVALID_ZIP")
        entries, directory = struct.unpack_from("<QQ", record, 32)
    elif entries == 0xFFFF or directory == 0xFFFFFFFF:
        raise ZipRefused("INVALID_ZIP")
    return entries, directory, end


def declared_directory(path: Path) -> tuple[int, int]:
    """(entries, central-directory bytes) as the end record declares them."""
    entries, directory, _ = _end_record(path)
    return entries, directory


def check_directory(path: Path, *, max_entries: int) -> int:
    """Walks the central directory record by record, without loading it, and returns the number of
    records. CPython reads the directory by its declared *size* and not its declared count, so a
    lying end record could otherwise make `ZipFile` build millions of entries first. The walk stops
    at `max_entries + 1` records, and the records must end exactly where the directory is declared
    to end and match the declared count. Raises ZipRefused."""
    entries, directory, end = _end_record(path)
    if directory > max_entries * RECORD_BUDGET or directory > MAX_DIRECTORY_BYTES:
        raise ZipRefused("TOO_MANY_ENTRIES" if entries > max_entries else "INVALID_ZIP")
    start = end - directory
    if start < 0:
        raise ZipRefused("INVALID_ZIP")
    position, count = start, 0
    with path.open("rb", buffering=1024 * 1024) as fh:
        fh.seek(start)
        while position < end:
            head = fh.read(46)
            if len(head) < 46 or head[:4] != _CENTRAL:
                raise ZipRefused("INVALID_ZIP")
            name, extra, comment = struct.unpack_from("<HHH", head, 28)
            length = 46 + name + extra + comment
            if position + length > end:
                raise ZipRefused("INVALID_ZIP")
            count += 1
            if count > max_entries:
                raise ZipRefused("TOO_MANY_ENTRIES")
            position += length
            fh.seek(position)
    if position != end or count != entries:
        raise ZipRefused("INVALID_ZIP")
    return count


def _classify(info: zipfile.ZipInfo) -> tuple[bool, str | None]:
    """(is_dir, skip reason). Encrypted, symlink and special entries are never extracted."""
    mode = info.external_attr >> 16 if info.create_system == 3 else 0
    # Many writers store permission bits without a file type: that is a regular file.
    file_type = stat.S_IFMT(mode)
    is_dir = info.is_dir() or file_type == stat.S_IFDIR
    if info.flag_bits & 0x1 or info.flag_bits & 0x40:
        return is_dir, "ENCRYPTED"
    if file_type == stat.S_IFLNK:
        return is_dir, "SYMLINK"
    if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
        return is_dir, "UNSUPPORTED_TYPE"
    if not is_dir and info.compress_type not in SUPPORTED_METHODS:
        return is_dir, "UNSUPPORTED_COMPRESSION"
    return is_dir, None


def _mtime_ms(info: zipfile.ZipInfo) -> int | None:
    try:
        return calendar.timegm(info.date_time + (0, 0, 0)) * 1000
    except (ValueError, OverflowError):
        return None


def inspect_zip(path: Path, *, max_entries: int) -> list[ZipItem]:
    """Lists the archive. Raises ZipRefused (INVALID_ZIP, TOO_MANY_ENTRIES) before `ZipFile` loads
    a central directory that is not plausible."""
    check_directory(path, max_entries=max_entries)
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError, ValueError, struct.error, EOFError) as exc:
        raise ZipRefused("INVALID_ZIP") from exc
    if len(infos) > max_entries:
        raise ZipRefused("TOO_MANY_ENTRIES")
    items: list[ZipItem] = []
    for index, info in enumerate(infos):
        is_dir, skip = _classify(info)
        items.append(
            ZipItem(
                index=index,
                filename=info.filename,
                is_dir=is_dir,
                size=0 if is_dir else info.file_size,
                compress_size=info.compress_size,
                mtime_ms=_mtime_ms(info),
                skip=skip,
            )
        )
    return items


def limited_chunks(
    stream: IO[bytes], *, compress_size: int, max_bytes: int, max_ratio: int
) -> Iterator[bytes]:
    """Yields decompressed chunks, raising EntryLimit when the entry exceeds `max_bytes` or expands
    past `max_ratio` times its compressed size (once it is larger than 1 MiB)."""
    total = 0
    allowed_ratio = max(compress_size, 1) * max_ratio
    while chunk := stream.read(CHUNK):
        total += len(chunk)
        if total > max_bytes:
            raise EntryLimit("UPLOAD_TOO_LARGE")
        if total > RATIO_FREE_BYTES and total > allowed_ratio:
            raise EntryLimit("DECOMPRESSION_RATIO")
        yield chunk
