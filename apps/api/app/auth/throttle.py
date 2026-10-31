"""Failed-attempt throttling backed by the database (survives restarts, shared by replicas).

Keys are hashed. Three windows apply to logins: account+IP (stops a single client), account
(stops distributed guessing) and IP (stops spraying many accounts). The per-account window means
an attacker can temporarily lock a victim out of password login; that is a deliberate trade
against unlimited guessing and is bounded by the window. Blocked attempts never reach password
verification, so a correct password during a lockout is also refused.
"""

import hashlib
import math
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth import clock
from app.config import Settings
from app.db.models import LoginFailure

Key = tuple[str, int]


def _h(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


class Throttle:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._window = timedelta(seconds=settings.throttle_window_seconds)

    def login_keys(self, email: str, ip: str) -> list[Key]:
        s = self._s
        return [
            (_h("acct-ip", email, ip), s.throttle_max_per_account_ip),
            (_h("acct", email), s.throttle_max_per_account),
            (_h("ip", ip), s.throttle_max_per_ip),
        ]

    def ip_keys(self, kind: str, ip: str) -> list[Key]:
        return [(_h(kind, ip), self._s.throttle_max_per_ip)]

    def retry_after(self, db: Session, keys: list[Key]) -> int | None:
        """Seconds until a blocked caller may retry, or None if not blocked."""
        now = clock.utcnow()
        cutoff = now - self._window
        db.execute(delete(LoginFailure).where(LoginFailure.attempted_at < cutoff))
        wait = 0
        for key, limit in keys:
            times = db.scalars(
                select(LoginFailure.attempted_at)
                .where(LoginFailure.key_hash == key, LoginFailure.attempted_at >= cutoff)
                .order_by(LoginFailure.attempted_at)
            ).all()
            if len(times) >= limit:
                release = times[len(times) - limit] + self._window
                wait = max(wait, math.ceil((release - now).total_seconds()))
        return max(wait, 1) if wait else None

    def record_failure(self, db: Session, keys: list[Key]) -> None:
        now = clock.utcnow()
        db.add_all(LoginFailure(key_hash=key, attempted_at=now) for key, _ in keys)

    def clear(self, db: Session, keys: list[Key]) -> None:
        db.execute(delete(LoginFailure).where(LoginFailure.key_hash.in_([k for k, _ in keys])))
