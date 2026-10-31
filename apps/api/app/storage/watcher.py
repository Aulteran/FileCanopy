"""Linux inotify watcher through ctypes (ADR-011 §7). Events are hints only: they name a directory
node that should be rescanned soon; identity decisions come from the scan.

A watch is placed on each directory through its open descriptor (`/proc/self/fd/N`), so a symlink
swapped into the path cannot redirect it. The watcher turns itself off for a root (periodic rescans
remain the guarantee) when the directory count nears `fs.inotify.max_user_watches`, and reports a
queue overflow so the caller can run a full pass.
"""

import ctypes
import logging
import os
import select
import struct
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from app.storage.resolver import StorageError, open_dir

log = logging.getLogger("filecanopy.watcher")

IN_MODIFY, IN_ATTRIB, IN_CLOSE_WRITE = 0x2, 0x4, 0x8
IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE = 0x40, 0x80, 0x100, 0x200
IN_DELETE_SELF, IN_MOVE_SELF = 0x400, 0x800
IN_Q_OVERFLOW, IN_IGNORED = 0x4000, 0x8000
IN_ONLYDIR = 0x01000000
IN_NONBLOCK, IN_CLOEXEC = 0o4000, 0o2000000
MASK = (
    IN_MODIFY
    | IN_ATTRIB
    | IN_CLOSE_WRITE
    | IN_MOVED_FROM
    | IN_MOVED_TO
    | IN_CREATE
    | IN_DELETE
    | IN_DELETE_SELF
    | IN_MOVE_SELF
    | IN_ONLYDIR
)
HEADER = struct.Struct("iIII")
WATCH_BUDGET = 0.8
MAX_WATCHES_FILE = Path("/proc/sys/fs/inotify/max_user_watches")


def max_user_watches() -> int | None:
    try:
        return int(MAX_WATCHES_FILE.read_text())
    except (OSError, ValueError):
        return None


class Watcher:
    def __init__(
        self,
        on_dirty: Callable[[uuid.UUID, uuid.UUID], None],
        on_overflow: Callable[[], None],
    ) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        self._init = getattr(libc, "inotify_init1", None)
        self._add = getattr(libc, "inotify_add_watch", None)
        self._rm = getattr(libc, "inotify_rm_watch", None)
        self.available = self._init is not None and self._add is not None
        self.on_dirty, self.on_overflow = on_dirty, on_overflow
        self._fd = -1
        self._lock = threading.Lock()
        self._wds: dict[int, tuple[uuid.UUID, uuid.UUID]] = {}
        self._by_root: dict[uuid.UUID, set[int]] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> bool:
        if self._thread is not None:
            return True
        if not self.available or self._init is None:
            return False
        self._fd = self._init(IN_NONBLOCK | IN_CLOEXEC)
        if self._fd < 0:
            self.available = False
            return False
        self._thread = threading.Thread(target=self._loop, name="fc-watcher", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def watching(self, root_id: uuid.UUID) -> bool:
        with self._lock:
            return bool(self._by_root.get(root_id))

    def forget(self, root_id: uuid.UUID) -> None:
        """Drops every watch of a root (it is on a network filesystem, or too large)."""
        with self._lock:
            for wd in self._by_root.pop(root_id, set()):
                self._wds.pop(wd, None)
                if self._rm is not None and self._fd >= 0:
                    self._rm(self._fd, wd)

    def sync(
        self,
        library: Path,
        root_id: uuid.UUID,
        relative_dir: str,
        directories: list[tuple[uuid.UUID, list[str]]],
    ) -> bool:
        """Places watches on a root's directories. Returns False (and watches nothing) if the
        directory count would use most of the user's inotify watches."""
        if not self.available or self._fd < 0 or self._add is None:
            return False
        limit = max_user_watches()
        self.forget(root_id)
        if limit is not None and len(directories) > WATCH_BUDGET * limit:
            log.warning("too many directories to watch; relying on periodic rescans")
            return False
        added: set[int] = set()
        for node_id, comps in directories:
            try:
                with open_dir(library, relative_dir, comps) as dir_fd:
                    wd = self._add(self._fd, f"/proc/self/fd/{dir_fd}".encode(), MASK)
            except (StorageError, OSError):
                continue
            if wd >= 0:
                with self._lock:
                    self._wds[wd] = (root_id, node_id)
                added.add(wd)
        with self._lock:
            self._by_root[root_id] = added
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self._fd], [], [], 0.5)
                if not ready:
                    continue
                data = os.read(self._fd, 65536)
            except (OSError, ValueError):
                if self._stop.is_set():
                    return
                continue
            self._dispatch(data)

    def _dispatch(self, data: bytes) -> None:
        offset = 0
        while offset + HEADER.size <= len(data):
            wd, mask, _, length = HEADER.unpack_from(data, offset)
            offset += HEADER.size + length
            if mask & IN_Q_OVERFLOW:
                self.on_overflow()
                continue
            with self._lock:
                target = self._wds.get(wd)
                if mask & IN_IGNORED:
                    self._wds.pop(wd, None)
            if target is not None:
                self.on_dirty(*target)
