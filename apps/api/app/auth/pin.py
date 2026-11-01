"""Confidential PIN (M12b, docs/milestones/M12b-design-and-plan.md).

A six-digit PIN is brute-forceable, so the safety is in the counter, not in the hash:

* the counter belongs to the *account* (new sessions and new addresses do not reset it);
* an attempt is reserved (counter raised and committed) **before** the hash is compared, so
  parallel requests cannot get more tries than the schedule allows;
* from the third failure the next attempt waits (30 s doubling to 15 min); a blocked attempt never
  reaches the hash;
* the tenth failure disables the PIN until it is reset with a stronger proof;
* the hash is Argon2id over an HMAC of the PIN with a server-side pepper that lives outside the
  database, so a database-only leak cannot be attacked offline.

Nothing here knows about HTTP; routes translate `PinError`.
"""

import hashlib
import hmac
import os
import re
import secrets
import threading
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import clock
from app.auth.security import Passwords
from app.config import Settings
from app.db.models import UserPin

PIN_RE = re.compile(r"^[0-9]{6}$")
FREE_FAILURES = 2  # the third failure starts the waiting
FIRST_WAIT_SECONDS = 30
MAX_WAIT_SECONDS = 900
SESSION_END_AT = 5  # failures at which the signed-in session is ended (M09 behaviour)
DISABLE_AT = 10  # failures at which the PIN stops working until reset


class PinError(Exception):
    def __init__(self, code: str, status: int, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.retry_after = retry_after


def weak_reason(pin: str) -> str | None:
    """Why a PIN is refused as too easy, or None. Only mechanical patterns: no guessing about
    birthdays."""
    if not PIN_RE.match(pin):
        return "PIN_FORMAT"
    digits = [int(c) for c in pin]
    steps = {digits[i + 1] - digits[i] for i in range(5)}
    if len(set(pin)) == 1 or steps in ({1}, {-1}):
        return "PIN_TOO_EASY"
    if pin[:2] * 3 == pin or pin[:3] * 2 == pin:
        return "PIN_TOO_EASY"
    return None


def wait_after(failures: int) -> int:
    """Seconds the account must wait after this many consecutive failures."""
    if failures <= FREE_FAILURES:
        return 0
    return int(min(FIRST_WAIT_SECONDS * 2 ** (failures - FREE_FAILURES - 1), MAX_WAIT_SECONDS))


class PinService:
    def __init__(self, settings: Settings, passwords: Passwords) -> None:
        self._s = settings
        self._passwords = passwords
        self._pepper: bytes | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------- secret

    def _load_pepper(self) -> bytes:
        with self._lock:
            if self._pepper is not None:
                return self._pepper
            configured = self._s.pin_pepper.get_secret_value()
            if configured:
                if len(configured) < 32:
                    raise PinError("PIN_UNAVAILABLE", 503)
                self._pepper = configured.encode()
                return self._pepper
            path = Path(self._s.app_data_path) / "pin-pepper"
            try:
                self._pepper = bytes.fromhex(path.read_text().strip())
            except FileNotFoundError:
                fresh = secrets.token_hex(32)
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:  # another process won
                    self._pepper = bytes.fromhex(path.read_text().strip())
                else:
                    with os.fdopen(fd, "w") as f:
                        f.write(fresh)
                    self._pepper = bytes.fromhex(fresh)
            except (OSError, ValueError) as exc:
                raise PinError("PIN_UNAVAILABLE", 503) from exc
            if len(self._pepper) < 32:
                self._pepper = None
                raise PinError("PIN_UNAVAILABLE", 503)
            return self._pepper

    def _digest(self, user_id: uuid.UUID, pin: str) -> str:
        msg = f"{user_id}\x1f{pin}".encode()
        return hmac.new(self._load_pepper(), msg, hashlib.sha256).hexdigest()

    # --------------------------------------------------------------- state

    def row(self, db: Session, user_id: uuid.UUID) -> UserPin | None:
        return db.get(UserPin, user_id)

    def is_set(self, db: Session, user_id: uuid.UUID) -> bool:
        return db.get(UserPin, user_id) is not None

    def set(self, db: Session, user_id: uuid.UUID, pin: str) -> None:
        """Stores a new PIN (the caller has already checked a proof). Clears the counter."""
        reason = weak_reason(pin)
        if reason:
            raise PinError(reason, 422)
        hashed = self._passwords.hash(self._digest(user_id, pin))
        now = clock.utcnow()
        row = db.get(UserPin, user_id)
        if row is None:
            db.add(UserPin(user_id=user_id, pin_hash=hashed, set_at=now))
        else:
            row.pin_hash, row.set_at = hashed, now
            row.failures, row.last_failure_at, row.blocked_until, row.disabled = (
                0,
                None,
                None,
                False,
            )

    def remove(self, db: Session, user_id: uuid.UUID) -> bool:
        row = db.get(UserPin, user_id)
        if row is None:
            return False
        db.delete(row)
        return True

    def blocked_for(self, row: UserPin) -> int | None:
        if row.blocked_until is None:
            return None
        left = (row.blocked_until - clock.utcnow()).total_seconds()
        return int(left) + 1 if left > 0 else None

    # -------------------------------------------------------------- verify

    def verify(self, db: Session, user_id: uuid.UUID, pin: str) -> int:
        """Returns 0 on success. Raises PinError for: no PIN (409), disabled (403), waiting (429),
        wrong PIN (403, `retry_after` unused). On a wrong PIN the exception carries the failure
        count in `failures`. The reservation is committed before the comparison."""
        now = clock.utcnow()
        row = db.scalar(select(UserPin).where(UserPin.user_id == user_id).with_for_update())
        if row is None:
            db.rollback()
            raise PinError("PIN_NOT_SET", 409)
        if row.disabled:
            db.rollback()
            raise PinError("PIN_DISABLED", 403)
        if row.blocked_until is not None and row.blocked_until > now:
            wait = self.blocked_for(row) or 1
            db.rollback()
            raise PinError("PIN_BLOCKED", 429, wait)
        # Reserve the attempt: counted and committed before the PIN is looked at.
        row.failures += 1
        row.last_failure_at = now
        wait = wait_after(row.failures)
        row.blocked_until = now + timedelta(seconds=wait) if wait else None
        stored, count = row.pin_hash, row.failures
        db.commit()
        ok = PIN_RE.match(pin) is not None and self._passwords.verify(
            stored, self._digest(user_id, pin)
        )
        row = db.scalar(select(UserPin).where(UserPin.user_id == user_id).with_for_update())
        if row is None:  # removed meanwhile
            db.rollback()
            raise PinError("PIN_NOT_SET", 409)
        if ok:
            if row.pin_hash == stored:  # not replaced while we were comparing
                row.failures, row.last_failure_at, row.blocked_until = 0, None, None
            db.commit()
            return 0
        if count >= DISABLE_AT:
            row.disabled = True
        db.commit()
        err = PinError("INVALID_PIN", 403)
        err.failures = count  # type: ignore[attr-defined]
        raise err
