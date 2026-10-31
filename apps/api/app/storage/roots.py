"""Startup reconciliation of root directories with their database rows (ADR-004 §1, ADR-011 §3, §8).

Every root must live on the same device as the library mount: a drive mounted over a root would
point a personal folder (or the shared tree) at other host content, so it is refused, never
scanned and never written.
"""

import logging
import os
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import StorageRoot
from app.storage.resolver import StorageError, ensure_root_dir, open_dir

log = logging.getLogger("filecanopy.storage")

NETWORK_FS_TYPES = frozenset({"nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.sshfs", "afs", "9p"})
MOUNTINFO = Path("/proc/self/mountinfo")


def _unescape(field: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def filesystem_type(path: Path, mountinfo: Path = MOUNTINFO) -> str | None:
    """The filesystem type of the mount containing `path`: the longest mount-point prefix in
    `/proc/self/mountinfo`. None if it cannot be read (non-Linux, restricted /proc)."""
    try:
        target = os.fspath(path.resolve())
        best_len, best_type = -1, None
        for line in mountinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            head, _, tail = line.partition(" - ")
            fields, rest = head.split(), tail.split()
            if len(fields) < 5 or not rest:
                continue
            mount_point = _unescape(fields[4])
            inside = target == mount_point or target.startswith(mount_point.rstrip("/") + "/")
            if inside and len(mount_point) > best_len:
                best_len, best_type = len(mount_point), rest[0]
        return best_type
    except OSError:
        return None


def is_network_fs(fs_type: str | None, setting: str) -> bool:
    if setting == "yes":
        return True
    if setting == "no":
        return False
    return fs_type in NETWORK_FS_TYPES


def root_device(library: Path, relative_dir: str) -> int:
    """`st_dev` of the root directory, opened through the no-symlink resolver."""
    with open_dir(library, relative_dir) as fd:
        return os.fstat(fd).st_dev


def ensure_roots(sessions: "sessionmaker[Session]", settings: Settings) -> dict[str, int]:
    """Creates missing root directories (0o750) and marks unusable ones `missing` instead of
    crashing. A symlinked or non-directory root is refused, never followed, and so is a root on a
    different device than the library (`unsupported_mount`). Admin-disabled roots are left alone.
    Records the filesystem type. Returns counts by resulting status."""
    counts = {"active": 0, "missing": 0, "unsupported_mount": 0}
    library = settings.library_path
    fs_type = filesystem_type(library)
    network = is_network_fs(fs_type, settings.network_fs)
    with sessions() as db:
        try:
            library_dev: int | None = os.stat(library).st_dev
        except OSError:
            library_dev = None
        for root in db.scalars(select(StorageRoot).where(StorageRoot.status != "disabled")):
            try:
                ensure_root_dir(library, root.relative_dir)
                if (
                    library_dev is not None
                    and root_device(library, root.relative_dir) != library_dev
                ):
                    status = "unsupported_mount"
                    log.warning("storage root on another device refused (%s)", root.id)
                else:
                    status = "active"
            except (StorageError, OSError) as exc:
                status = "missing"
                log.warning("storage root unusable (%s): %s", root.id, type(exc).__name__)
            if root.status != status:
                root.status = status
            root.fs_type, root.network_fs = (fs_type or None) and fs_type[:32], network
            counts[status] += 1
            db.commit()  # one root at a time: a failure cannot undo the others
    return counts
