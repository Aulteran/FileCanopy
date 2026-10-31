"""Scheduler for backups (ADR-015 section 5): one daemon thread inside the API process, optional
and never needed for CRUD. It checks the settings every `backup_check_seconds`, runs the roots
that are due one at a time and leaves everything else alone."""

import logging
import threading
import time
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.auth import clock
from app.backup.engine import BackupEngine, RunResult
from app.backup.store import load_config
from app.config import Settings, backup_dir
from app.db.models import BackupRun, StorageRoot

log = logging.getLogger("filecanopy.backup")

MANUAL_MIN_INTERVAL = 60.0


class BackupService:
    def __init__(self, sessions: "sessionmaker[Session]", settings: Settings) -> None:
        self.sessions, self.settings = sessions, settings
        self.engine = BackupEngine(sessions, settings)
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_lock = threading.Lock()
        self._force = False
        self._last_manual = 0.0

    # -- lifecycle
    def start(self) -> None:
        if self._thread is not None or backup_dir(self.settings) is None:
            return
        try:
            self.engine.recover_interrupted()
        except Exception as exc:  # never blocks serving
            log.warning("backup recovery failed: %s", type(exc).__name__)
        if not self.settings.backup_scheduler_enabled:
            return
        self._thread = threading.Thread(target=self._loop, name="fc-backup", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.engine.stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    # -- inputs
    def request_run(self) -> bool:
        """An administrator asked for a run now; False if asked less than a minute ago."""
        now = time.monotonic()
        if self._last_manual and now - self._last_manual < MANUAL_MIN_INTERVAL:
            return False
        self._last_manual = now
        self._force = True
        self._wake.set()
        return True

    # -- work
    def due(self, db: Session, root_id: uuid.UUID, interval_minutes: int) -> bool:
        last = db.scalar(
            select(func.max(BackupRun.started_at)).where(
                BackupRun.root_id == root_id, BackupRun.status != "running"
            )
        )
        return last is None or clock.utcnow() - last >= timedelta(minutes=interval_minutes)

    def tick(self, *, force: bool = False) -> list[tuple[uuid.UUID, RunResult]]:
        """Runs every due root once. Returns what ran (skipped roots included)."""
        results: list[tuple[uuid.UUID, RunResult]] = []
        if not self._tick_lock.acquire(blocking=False):
            return results
        try:
            with self.sessions() as db:
                cfg = load_config(db)
                if not cfg.enabled:
                    return results
                root_ids = list(db.scalars(select(StorageRoot.id).order_by(StorageRoot.created_at)))
                todo = [r for r in root_ids if force or self.due(db, r, cfg.interval_minutes)]
            for root_id in todo:
                if self.engine.stop.is_set():
                    break
                results.append((root_id, self.engine.run_root(root_id)))
        finally:
            self._tick_lock.release()
        return results

    def _loop(self) -> None:
        while not self.engine.stop.is_set():
            try:
                force, self._force = self._force, False
                self.tick(force=force)
            except Exception as exc:  # a failed pass must never kill the thread
                log.error("backup pass failed: %s", type(exc).__name__)
            self._wake.wait(self.settings.backup_check_seconds)
            self._wake.clear()
