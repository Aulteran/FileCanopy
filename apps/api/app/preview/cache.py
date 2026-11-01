"""Derivative cache under `app-data/previews/` (never inside the library, never in backups).

Layout: `previews/<node-id>/<key>.<ext>` where the key comes from the file's revision, size and
modification time, so a changed file never matches an old derivative. Reads of the cache always
happen after the node guard has passed; the cache has no URLs of its own.
"""

import hashlib
import logging
import os
import shutil
import time
import uuid
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Node

log = logging.getLogger("filecanopy.preview")

KEEP_STATUSES = ("active", "busy", "reconcile_needed")
EXTS = ("pdf", "json", "jpg")
STALE_TMP_SECONDS = 3600


def make_key(revision: int, mtime_ns: int, size: int) -> str:
    return hashlib.sha256(f"{revision}:{mtime_ns}:{size}".encode()).hexdigest()[:20]


class PreviewCache:
    def __init__(self, app_data: Path, max_bytes: int) -> None:
        self.base = app_data / "previews"
        self.max_bytes = max_bytes

    def _ensure(self, path: Path) -> None:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)

    def node_dir(self, node_id: uuid.UUID) -> Path:
        return self.base / str(node_id)

    def path(self, node_id: uuid.UUID, key: str, ext: str) -> Path:
        return self.node_dir(node_id) / f"{key}.{ext}"

    def tmp_path(self) -> Path:
        tmp = self.base / ".tmp"
        self._ensure(tmp)
        return tmp / uuid.uuid4().hex

    def get(self, node_id: uuid.UUID, key: str, ext: str) -> Path | None:
        p = self.path(node_id, key, ext)
        try:
            if p.is_file() and not p.is_symlink():
                os.utime(p)  # least recently used goes first when trimming
                return p
        except OSError:
            return None
        return None

    def put_file(self, node_id: uuid.UUID, key: str, ext: str, tmp: Path) -> Path:
        """Moves a finished temp file into place and drops older versions of the same kind."""
        d = self.node_dir(node_id)
        self._ensure(d)
        dest = self.path(node_id, key, ext)
        os.replace(tmp, dest)
        for other in d.glob(f"*.{ext}"):
            if other != dest:
                other.unlink(missing_ok=True)
        return dest

    def put_bytes(self, node_id: uuid.UUID, key: str, ext: str, data: bytes) -> Path:
        tmp = self.tmp_path()
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(data)
            return self.put_file(node_id, key, ext, tmp)
        finally:
            tmp.unlink(missing_ok=True)

    def discard(self, node_ids: Iterable[uuid.UUID]) -> int:
        n = 0
        for nid in node_ids:
            d = self.node_dir(nid)
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        return n

    # ---------------------------------------------------------------- housekeeping

    def sweep(self, sessions: "sessionmaker[Session]") -> dict[str, int]:
        """Removes derivatives that must not outlive their file: unknown, trashed, purged, missing
        or dismissed nodes, older versions, stray temp files; then trims to the size cap."""
        removed = {"nodes": 0, "files": 0, "trimmed": 0}
        if not self.base.is_dir():
            return removed
        tmp = self.base / ".tmp"
        if tmp.is_dir():
            for f in tmp.iterdir():
                try:
                    if time.time() - f.stat().st_mtime > STALE_TMP_SECONDS:
                        f.unlink()
                        removed["files"] += 1
                except OSError:
                    pass
        dirs: dict[uuid.UUID, Path] = {}
        for d in self.base.iterdir():
            if d.name == ".tmp":
                continue
            try:
                dirs[uuid.UUID(d.name)] = d
            except ValueError:
                shutil.rmtree(d, ignore_errors=True) if d.is_dir() else d.unlink(missing_ok=True)
                removed["files"] += 1
        ids = list(dirs)
        keep: set[uuid.UUID] = set()
        with sessions() as db:
            for i in range(0, len(ids), 500):
                chunk = ids[i : i + 500]
                keep.update(
                    db.scalars(
                        select(Node.id).where(Node.id.in_(chunk), Node.status.in_(KEEP_STATUSES))
                    )
                )
        for nid, d in dirs.items():
            if nid not in keep:
                shutil.rmtree(d, ignore_errors=True)
                removed["nodes"] += 1
                continue
            for ext in EXTS:  # keep only the newest version of each kind
                found = sorted(d.glob(f"*.{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
                for old in found[1:]:
                    old.unlink(missing_ok=True)
                    removed["files"] += 1
        removed["trimmed"] = self._trim()
        return removed

    def _trim(self) -> int:
        files: list[tuple[float, int, Path]] = []
        total = 0
        for p in self.base.glob("*/*"):
            try:
                st = p.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        trimmed = 0
        for _, size, p in sorted(files):
            if total <= self.max_bytes:
                break
            p.unlink(missing_ok=True)
            total -= size
            trimmed += 1
        return trimmed


def discard_trash_item(db: Session, settings: Settings, item_id: uuid.UUID) -> None:
    """Drops the derivatives of every node held by a trash item (called when it is trashed and
    again when it is purged). Best effort: the hourly sweep removes whatever this misses."""
    try:
        ids = list(db.scalars(select(Node.id).where(Node.trash_item_id == item_id)))
        PreviewCache(settings.app_data_path, settings.preview_cache_max_bytes).discard(ids)
    except Exception as exc:  # noqa: BLE001 - housekeeping must never fail a delete
        log.warning("preview discard failed: %s", type(exc).__name__)
