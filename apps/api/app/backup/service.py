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
from app.backup.jobs import all_specs
from app.backup.restore import sweep_stale
from app.backup.volume import inspect_volume
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
        self._only: frozenset[uuid.UUID | None] | None = None
        self._last_manual = 0.0

    # -- lifecycle
    def start(self) -> None:
        if self._thread is not None or backup_dir(self.settings) is None:
            return
        try:
            self.engine.recover_interrupted()
        except Exception as exc:  # never blocks serving
            log.warning("backup recovery failed: %s", type(exc).__name__)
        try:  # half-built restore folders of a crashed run (staging names, never shown)
            sweep_stale(self.sessions, self.settings)
        except Exception as exc:
            log.warning("restore cleanup failed: %s", type(exc).__name__)
        if inspect_volume(self.settings).reason == "nested":
            log.error("backup volume is the library, the app data or a storage root: runs refused")
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
    def request_run(self, jobs: "frozenset[uuid.UUID | None] | None" = None) -> bool:
        """An administrator asked for a run now; False if asked less than a minute ago. `jobs`
        limits it to those jobs (None inside the set = the default job); None = every job."""
        now = time.monotonic()
        if self._last_manual and now - self._last_manual < MANUAL_MIN_INTERVAL:
            return False
        self._last_manual = now
        self._force = True
        self._only = jobs
        self._wake.set()
        return True

    # -- work
    def due(
        self,
        db: Session,
        root_id: uuid.UUID,
        interval_minutes: int,
        job_id: uuid.UUID | None = None,
    ) -> bool:
        job_match = BackupRun.job_id.is_(None) if job_id is None else BackupRun.job_id == job_id
        last = db.scalar(
            select(func.max(BackupRun.started_at)).where(
                BackupRun.root_id == root_id, BackupRun.status != "running", job_match
            )
        )
        return last is None or clock.utcnow() - last >= timedelta(minutes=interval_minutes)

    def tick(
        self, *, force: bool = False, jobs: "frozenset[uuid.UUID | None] | None" = None
    ) -> list[tuple[uuid.UUID, RunResult]]:
        """Runs every due root once for every enabled job (the default job first; `jobs` limits
        it to those). Returns what ran (skipped roots included)."""
        results: list[tuple[uuid.UUID, RunResult]] = []
        if not self._tick_lock.acquire(blocking=False):
            return results
        try:
            plan: list[tuple[uuid.UUID | None, uuid.UUID]] = []
            with self.sessions() as db:
                roots = list(
                    db.execute(
                        select(StorageRoot.id, StorageRoot.kind).order_by(StorageRoot.created_at)
                    )
                )
                for spec in all_specs(db):
                    if not spec.enabled or (jobs is not None and spec.job_id not in jobs):
                        continue
                    plan.extend(
                        (spec.job_id, rid)
                        for rid, kind in roots
                        if spec.scope in ("all", kind)
                        and (force or self.due(db, rid, spec.interval_minutes, spec.job_id))
                    )
            for job_id, root_id in plan:
                if self.engine.stop.is_set():
                    break
                results.append((root_id, self.engine.run_root(root_id, job_id)))
        finally:
            self._tick_lock.release()
        return results

    def _loop(self) -> None:
        while not self.engine.stop.is_set():
            try:
                force, self._force = self._force, False
                only, self._only = self._only, None
                self.tick(force=force, jobs=only)
            except Exception as exc:  # a failed pass must never kill the thread
                log.error("backup pass failed: %s", type(exc).__name__)
            self._wake.wait(self.settings.backup_check_seconds)
            self._wake.clear()
