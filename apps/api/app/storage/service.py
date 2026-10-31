"""Background scheduling for the reconciler (ADR-011 §7): a startup pass, a periodic rescan every
`rescan_interval_seconds`, "rescan now" requests, debounced watcher hints, and throttled hashing.
One daemon thread works on one root at a time. Nothing here blocks serving or is required for CRUD.
"""

import logging
import threading
import time
import uuid
from collections import deque

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Node, StorageRoot
from app.domain.imports import prune_import_reports
from app.domain.nodes import path_components
from app.storage.reconcile import Reconciler
from app.storage.watcher import Watcher

log = logging.getLogger("filecanopy.reconcile")

DEBOUNCE_SECONDS = 2.0
MANUAL_MIN_INTERVAL = 60.0


class ReconcilerService:
    def __init__(self, sessions: "sessionmaker[Session]", settings: Settings) -> None:
        self.sessions, self.settings = sessions, settings
        self.reconciler = Reconciler(sessions, settings)
        self.watcher = Watcher(self._dirty, self._overflow) if settings.watcher_enabled else None
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._manual: deque[tuple[uuid.UUID, uuid.UUID]] = deque()
        self._forced: deque[uuid.UUID] = deque()
        self._last_manual: dict[uuid.UUID, float] = {}
        self._dirty_nodes: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
        self._full_pass = False
        self._hash_roots: set[uuid.UUID] = set()

    # -- lifecycle
    def start(self) -> None:
        if self._thread is not None:
            return
        if self.watcher is not None:
            self.watcher.start()
        self._thread = threading.Thread(target=self._loop, name="fc-reconciler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.reconciler.stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self.watcher is not None:
            self.watcher.stop()

    # -- inputs
    def request_rescan(self, root_id: uuid.UUID, node_id: uuid.UUID) -> bool:
        """Queues a subtree rescan; False if this node was rescanned less than a minute ago."""
        now = time.monotonic()
        with self._lock:
            last = self._last_manual.get(node_id)
            if last is not None and now - last < MANUAL_MIN_INTERVAL:
                return False
            self._last_manual[node_id] = now
            self._manual.append((root_id, node_id))
        self._wake.set()
        return True

    def request_apply(self, root_id: uuid.UUID) -> None:
        """A person decided to apply the changes of a suspended root: run a pass past the guard."""
        with self._lock:
            self._forced.append(root_id)
        self._wake.set()

    def _dirty(self, root_id: uuid.UUID, node_id: uuid.UUID) -> None:
        with self._lock:
            self._dirty_nodes[(root_id, node_id)] = time.monotonic()
        self._wake.set()

    def _overflow(self) -> None:
        self._full_pass = True
        self._wake.set()

    # -- work
    def process_requests(
        self, *, now: float | None = None, debounce: float = DEBOUNCE_SECONDS
    ) -> int:
        """Runs queued manual rescans and debounced watcher hints. Returns how many ran."""
        current = time.monotonic() if now is None else now
        with self._lock:
            jobs = list(self._manual)
            self._manual.clear()
            forced = list(self._forced)
            self._forced.clear()
            due = [k for k, t in self._dirty_nodes.items() if current - t >= debounce]
            for key in due:
                del self._dirty_nodes[key]
        for root_id in forced:
            self.reconciler.run_root(root_id, "manual", force=True)
        for root_id, node_id in jobs:
            self.reconciler.run_root(root_id, "manual", subtree=node_id)
        for root_id, node_id in due:
            self.reconciler.run_root(root_id, "hint", subtree=node_id)
        return len(forced) + len(jobs) + len(due)

    def full_pass(self, trigger: str) -> None:
        results = self.reconciler.run_all(trigger)
        for root_id, result in results.items():
            if result.outcome in ("ok", "partial"):
                self._hash_roots.add(root_id)
            self.sync_watches(root_id)

    def sync_watches(self, root_id: uuid.UUID) -> None:
        if self.watcher is None or not self.watcher.available:
            return
        with self.sessions() as db:
            root = db.get(StorageRoot, root_id)
            if root is None or root.status != "active":
                return
            if root.network_fs:  # events are not delivered reliably on network filesystems
                self.watcher.forget(root_id)
                return
            dirs = [
                (n.id, path_components(db, n))
                for n in db.scalars(
                    select(Node).where(
                        Node.root_id == root_id, Node.kind == "dir", Node.status == "active"
                    )
                )
            ]
            relative = root.relative_dir
        self.watcher.sync(self.settings.library_path, root_id, relative, dirs)

    def _prune_reports(self) -> None:
        """Retention for import reports, with the other periodic work (ADR-011, M06b review)."""
        try:
            with self.sessions() as db:
                prune_import_reports(db, self.settings)
        except Exception:  # never blocks scanning
            log.exception("pruning import reports failed")

    def _loop(self) -> None:
        trigger, next_full = "startup", time.monotonic()
        while not self.reconciler.stop.is_set():
            try:
                if time.monotonic() >= next_full or self._full_pass:
                    self._full_pass = False
                    self.full_pass(trigger)
                    trigger = "periodic"
                    self._prune_reports()
                    next_full = time.monotonic() + self.settings.rescan_interval_seconds
                self.process_requests()
                for root_id in list(self._hash_roots):
                    if self.reconciler.hash_pending(root_id, limit=20) == 0:
                        self._hash_roots.discard(root_id)
                    if self._wake.is_set():
                        break
            except Exception:  # a failed pass must never kill the thread
                log.exception("reconciler pass failed")
            self._wake.wait(1.0 if (self._hash_roots or self._dirty_nodes) else 5.0)
            self._wake.clear()
