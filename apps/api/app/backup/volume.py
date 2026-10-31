"""The backup volume (ADR-015 section 2). The app writes only a `filecanopy-backups/` folder in
it. Nesting with the library or app data is refused at startup by `Settings`; this module checks
what can only be seen at run time."""

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, backup_dir, path_identity

BACKUP_DIR = "filecanopy-backups"


@dataclass(frozen=True)
class VolumeStatus:
    configured: bool
    usable: bool
    reason: (
        str | None
    )  # not_configured | missing | not_a_directory | symlink | not_writable | nested
    same_device: bool | None
    free_bytes: int | None
    total_bytes: int | None


def inspect_volume(settings: Settings) -> VolumeStatus:
    path = backup_dir(settings)
    if path is None:
        return VolumeStatus(False, False, "not_configured", None, None, None)

    def bad(reason: str) -> VolumeStatus:
        return VolumeStatus(True, False, reason, None, None, None)

    try:
        info = os.lstat(path)
    except OSError:
        return bad("missing")
    if stat.S_ISLNK(info.st_mode):
        return bad("symlink")
    if not stat.S_ISDIR(info.st_mode):
        return bad("not_a_directory")
    if not os.access(path, os.W_OK | os.X_OK):
        return bad("not_writable")
    if nested_with_library(settings, path):
        return bad("nested")
    try:
        same = os.stat(path).st_dev == os.stat(settings.library_path).st_dev
    except OSError:
        same = None
    usage = shutil.disk_usage(path)
    return VolumeStatus(True, True, None, same, usage.free, usage.total)


def _managed_directories(settings: Settings) -> list[Path]:
    """The library, the app data, and every storage root directory found under the library."""
    library = settings.library_path
    found = [library, settings.app_data_path]
    try:
        for top in ("shared", "personal"):
            base = library / top
            if top == "shared":
                found.append(base)
            elif base.is_dir() and not base.is_symlink():
                with os.scandir(base) as entries:
                    found.extend(Path(e.path) for e in entries if e.is_dir(follow_symlinks=False))
    except OSError:
        pass
    return found


def nested_with_library(settings: Settings, path: Path) -> bool:
    """True when the backup directory *is* the library, the app data or a storage root, reached
    through another path (a second bind mount of the same folder). Path-based nesting is refused
    by `Settings` at startup; this compares (st_dev, st_ino), which a path comparison cannot."""
    ident = path_identity(path)
    if ident is None:
        return False
    return any(ident == path_identity(other) for other in _managed_directories(settings))


def backups_root(settings: Settings, *, create: bool = False) -> Path:
    path = backup_dir(settings)
    if path is None:
        raise RuntimeError("no backup volume configured")
    root = path / BACKUP_DIR
    if create:
        root.mkdir(mode=0o700, exist_ok=True)
    return root


def folder_usage(root: Path) -> int:
    """Bytes of everything the app keeps in its backup folder (archives, indexes, leftovers)."""
    total = 0
    if not root.exists():
        return 0
    for current, _dirs, names in os.walk(root, followlinks=False):
        for name in names:
            try:
                total += os.lstat(os.path.join(current, name)).st_size
            except OSError:
                continue
    return total
