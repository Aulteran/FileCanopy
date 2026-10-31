"""Filesystem identity helpers (ADR-011 §6). Device and inode numbers can exceed a signed 64-bit
column on some filesystems, so they are stored two's-complement."""

import os


def s64(value: int) -> int:
    return value - (1 << 64) if value >= (1 << 63) else value


def fingerprint(info: os.stat_result) -> tuple[int, int, int]:
    """(fs_dev, fs_ino, mtime_ns) as stored on a node."""
    return s64(info.st_dev), s64(info.st_ino), info.st_mtime_ns
