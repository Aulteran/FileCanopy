"""The mailbox poller (M13, ADR-007).

One lease row keeps a single poller at work. Each cycle opens the mailbox read-only, reads the
messages after the checkpoint in UID order, records each one (idempotently) and only then moves
the checkpoint. A message that keeps failing is recorded as an `error` after five attempts so it
cannot stall the mailbox; a failing mailbox is retried with a growing delay.
"""

import logging
import threading
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.auth import clock
from app.auth.service import audit
from app.config import Settings
from app.db.models import MailboxState
from app.mail import store
from app.mail.imap import ImapClient, MailboxClient, MailboxError

log = logging.getLogger("filecanopy.mail")

LEASE = timedelta(minutes=5)
MAX_ATTEMPTS = 5
MAX_BACKOFF = 900


def _lease(db: Session, key: str) -> MailboxState | None:
    now = clock.utcnow()
    if db.get(MailboxState, key) is None:
        db.add(MailboxState(mailbox_key=key, last_uid=0, failures=0, retry_count=0))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    won = db.execute(
        update(MailboxState)
        .where(
            MailboxState.mailbox_key == key,
            (MailboxState.lease_until.is_(None)) | (MailboxState.lease_until < now),
        )
        .values(lease_until=now + LEASE)
    )
    db.commit()
    if won.rowcount != 1:  # type: ignore[attr-defined]
        return None
    db.expire_all()
    return db.get(MailboxState, key)


def poll_once(
    sessions: "sessionmaker[Session]",
    settings: Settings,
    factory: Callable[[], MailboxClient] | None = None,
) -> int:
    """One cycle. Returns how many messages were recorded. Never raises for mailbox trouble: the
    state row carries the last error code."""
    key = store.mailbox_key(settings)
    recorded = 0
    with sessions() as db:
        state = _lease(db, key)
        if state is None:
            return 0
        client: MailboxClient | None = None
        try:
            client = factory() if factory else ImapClient(settings).connect()
            recorded = _read(db, settings, client, state)
            state.last_error, state.failures = None, 0
        except MailboxError as exc:
            db.rollback()
            state = db.get(MailboxState, key)
            if state is not None:
                state.last_error, state.failures = exc.code, state.failures + 1
        except Exception as exc:
            db.rollback()
            log.warning("mailbox poll failed: %s", type(exc).__name__)
            state = db.get(MailboxState, key)
            if state is not None:
                state.last_error, state.failures = "POLL_FAILED", state.failures + 1
        finally:
            if client is not None:
                client.close()
            state = db.get(MailboxState, key)
            if state is not None:
                state.lease_until = None
                state.last_poll_at = clock.utcnow()
            db.commit()
    store.sweep_retention(sessions, settings)
    return recorded


def _read(db: Session, settings: Settings, client: MailboxClient, state: MailboxState) -> int:
    folder = settings.imap_folder
    if any(c in folder for c in '"\\\r\n') or not folder:
        raise MailboxError("FOLDER_UNAVAILABLE")
    uidvalidity = client.select(folder)
    if state.uidvalidity != uidvalidity:
        # The server renumbered the mailbox: start over; content hashes absorb the re-read.
        state.uidvalidity, state.last_uid = uidvalidity, 0
        state.retry_uid, state.retry_count = None, 0
        db.commit()
    count = 0
    for uid in client.uids_after(state.last_uid, settings.imap_max_per_poll):
        try:
            size = client.size_of(uid)
            if size > settings.imap_max_message_bytes:
                store.record_unreadable(
                    db, settings, uidvalidity, uid, clock.utcnow(), size, "quarantined", "TOO_LARGE"
                )
            else:
                raw, when = client.fetch(uid)
                store.ingest(db, settings, uidvalidity, uid, store.internal_date_or_now(when), raw)
        except Exception as exc:
            db.rollback()
            state = db.get(MailboxState, state.mailbox_key) or state
            code = exc.code if isinstance(exc, MailboxError) else type(exc).__name__
            state.retry_count = state.retry_count + 1 if state.retry_uid == uid else 1
            state.retry_uid = uid
            if state.retry_count < MAX_ATTEMPTS:
                db.commit()
                raise
            log.warning("mailbox message gave up after %s attempts: %s", MAX_ATTEMPTS, code)
            store.record_unreadable(
                db, settings, uidvalidity, uid, clock.utcnow(), 0, "error", "PROCESSING_FAILED"
            )
            audit(db, "mail.dead_letter", reason="PROCESSING_FAILED")
            state.retry_uid, state.retry_count = None, 0
        state = db.get(MailboxState, state.mailbox_key) or state
        state.last_uid = uid
        db.commit()
        count += 1
    return count


class MailboxPoller:
    def __init__(self, settings: Settings, sessions: "sessionmaker[Session]") -> None:
        self.settings = settings
        self.sessions = sessions
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.settings.imap_configured or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="mailbox", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def poll_now(self) -> None:
        self._wake.set()

    def _delay(self) -> float:
        with self.sessions() as db:
            state = db.get(MailboxState, store.mailbox_key(self.settings))
            failures = state.failures if state else 0
        base = self.settings.imap_poll_seconds
        return float(min(base * (2 ** min(failures, 6)), max(base, MAX_BACKOFF)))

    def _run(self) -> None:
        self._stop.wait(5)  # let startup finish
        while not self._stop.is_set():
            try:
                poll_once(self.sessions, self.settings)
                delay = self._delay()
            except Exception as exc:  # never kill the thread
                log.warning("mailbox loop error: %s", type(exc).__name__)
                delay = float(self.settings.imap_poll_seconds)
            self._wake.wait(delay)
            self._wake.clear()
