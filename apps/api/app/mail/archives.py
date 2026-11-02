"""Limits on archive attachments, checked before they reach the library (M14, ADR-007).

Nothing is extracted or written. Zip-family files (zip, Office and OpenDocument documents, epub,
jar) are judged from their central directory: a plausible directory, a bounded entry count, a
bounded declared size, a bounded per-entry ratio and no archive inside an archive. gzip, bzip2 and
xz streams are decompressed into the void under the same size and ratio caps. Other archive
formats (7z, rar, tar, ...) are not opened by anything in the app, so they are stored like any
other file and only the upload size cap applies.
"""

import bz2
import gzip
import lzma
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from app.storage import ziparchive

ZIP_MAGIC = b"PK\x03\x04"
ZIP_EMPTY = b"PK\x05\x06"
GZIP_MAGIC = b"\x1f\x8b"
BZIP2_MAGIC = b"BZh"
XZ_MAGIC = b"\xfd7zXZ\x00"
OPAQUE_MAGICS = (b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07")
ARCHIVE_EXTENSIONS = frozenset(
    {".zip", ".jar", ".apk", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".tar", ".lzma", ".cab"}
)
CHUNK = 1024 * 1024
RATIO_FREE_BYTES = 1024 * 1024  # up to this size, ratio is not judged


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int
    max_uncompressed_bytes: int
    max_ratio: int


class ArchiveRefused(Exception):
    """The archive must not reach the library. `code` is stable (fits a 24-character column)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _head(path: Path, n: int = 8) -> bytes:
    with path.open("rb") as fh:
        return fh.read(n)


def kind_of(path: Path) -> str | None:
    """'zip', 'gzip', 'bzip2', 'xz', 'opaque' (another archive format) or None."""
    head = _head(path, 8)
    # zipfile finds the directory at the END of the file, so a zip with bytes in front of it
    # (self-extracting or polyglot) must be judged as a zip too.
    if head.startswith((ZIP_MAGIC, ZIP_EMPTY)) or zipfile.is_zipfile(path):
        return "zip"
    if head.startswith(GZIP_MAGIC):
        return "gzip"
    if head.startswith(BZIP2_MAGIC):
        return "bzip2"
    if head.startswith(XZ_MAGIC):
        return "xz"
    if head.startswith(OPAQUE_MAGICS):
        return "opaque"
    return None


def _looks_like_archive(name: str, head: bytes) -> bool:
    low = name.lower()
    dot = low.rfind(".")
    if dot >= 0 and low[dot:] in ARCHIVE_EXTENSIONS:
        return True
    return head.startswith(
        (ZIP_MAGIC, ZIP_EMPTY, GZIP_MAGIC, BZIP2_MAGIC, XZ_MAGIC, *OPAQUE_MAGICS)
    )


def _check_zip(path: Path, limits: ArchiveLimits) -> None:
    try:
        items = ziparchive.inspect_zip(path, max_entries=limits.max_entries)
    except ziparchive.ZipRefused as exc:
        raise ArchiveRefused(
            "ARCHIVE_TOO_MANY_ENTRIES" if exc.code == "TOO_MANY_ENTRIES" else "ARCHIVE_INVALID"
        ) from exc
    total = 0
    for item in items:
        if item.is_dir:
            continue
        total += item.size
        if total > limits.max_uncompressed_bytes:
            raise ArchiveRefused("ARCHIVE_TOO_LARGE")
        if (
            item.size > RATIO_FREE_BYTES
            and item.size > max(item.compress_size, 1) * limits.max_ratio
        ):
            raise ArchiveRefused("ARCHIVE_RATIO")
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            for item, info in zip(items, infos, strict=True):
                if item.is_dir or item.skip is not None:
                    continue  # encrypted or special entries cannot be looked into
                if info.file_size < 4:
                    continue
                with zf.open(info) as entry:
                    inner = entry.read(8)
                if _looks_like_archive(info.filename, inner):
                    raise ArchiveRefused("ARCHIVE_NESTED")
    except ArchiveRefused:
        raise
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError, RuntimeError) as exc:
        raise ArchiveRefused("ARCHIVE_INVALID") from exc


def _check_stream(opener: "IO[bytes] | _XzReader", compressed: int, limits: ArchiveLimits) -> None:
    total = 0
    allowed_ratio = max(compressed, 1) * limits.max_ratio
    try:
        while chunk := opener.read(CHUNK):
            total += len(chunk)
            if total > limits.max_uncompressed_bytes:
                raise ArchiveRefused("ARCHIVE_TOO_LARGE")
            if total > RATIO_FREE_BYTES and total > allowed_ratio:
                raise ArchiveRefused("ARCHIVE_RATIO")
            if total == len(chunk) and _looks_like_archive("", chunk[:8]):
                raise ArchiveRefused("ARCHIVE_NESTED")
    except ArchiveRefused:
        raise
    except (OSError, EOFError, lzma.LZMAError, ValueError) as exc:
        raise ArchiveRefused("ARCHIVE_INVALID") from exc


XZ_MEMLIMIT = 64 * 1024 * 1024


class _XzReader:
    """Reads an xz stream through a decoder with a memory limit: the dictionary size is declared
    in the (attacker-written) header and `lzma.open` would honour any value."""

    def __init__(self, path: Path) -> None:
        self._fh = path.open("rb")
        self._dec = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=XZ_MEMLIMIT)

    def read(self, n: int) -> bytes:
        while True:
            if self._dec.eof:
                return b""
            if self._dec.needs_input:
                data = self._fh.read(CHUNK)
                if not data:
                    raise EOFError("truncated xz stream")
            else:
                data = b""
            out = self._dec.decompress(data, max_length=n)
            if out:
                return out

    def close(self) -> None:
        self._fh.close()


def check(path: Path, name: str, limits: ArchiveLimits) -> None:
    """Raises ArchiveRefused when the file is an archive that breaks a limit. Files that are not
    archives, and archive formats nothing in the app opens, pass."""
    kind = kind_of(path)
    if kind is None or kind == "opaque":
        return
    if kind == "zip":
        _check_zip(path, limits)
        return
    size = path.stat().st_size
    if kind == "xz":
        reader = _XzReader(path)
        try:
            _check_stream(reader, size, limits)
        finally:
            reader.close()
        return
    opener: Callable[[Path, str], IO[bytes]] = gzip.open if kind == "gzip" else bz2.open  # type: ignore[assignment]
    with opener(path, "rb") as stream:
        _check_stream(stream, size, limits)
