"""Personal access tokens for the optional MCP interface (M21).

A token is `fcmcp_` plus 256 random bits. Only its SHA-256 is stored, so a copy of the database
cannot be used to call the interface. Every call re-reads the token row and the account: nothing
is cached, so revoking a token or disabling an account is effective on the very next request.
"""

import threading
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import clock
from app.auth.security import digest, new_token
from app.db.models import McpToken, User

PREFIX = "fcmcp_"
MAX_PRESENTED = 128  # longer values cannot be one of ours; never hashed
SCOPES = ("browse", "search", "text", "collections", "activity")
DEFAULT_SCOPES = ("browse", "search")  # text is an explicit choice
SEEN_GRANULARITY = timedelta(seconds=60)


def mint() -> tuple[str, str, str]:
    """(value shown once, hash stored, hint shown in lists)."""
    value = PREFIX + new_token()
    return value, digest(value), value[-4:]


def state_of(row: McpToken, now: datetime) -> str:
    if row.revoked_at is not None:
        return "revoked"
    return "expired" if row.expires_at <= now else "active"


def live_count(db: Session, user_id: uuid.UUID) -> int:
    now = clock.utcnow()
    rows = db.scalars(
        select(McpToken).where(McpToken.user_id == user_id, McpToken.revoked_at.is_(None))
    ).all()
    return sum(1 for r in rows if r.expires_at > now)


class Rejected(Exception):
    """Why a presented token was refused. The reason is audited; the caller only sees 401."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def authenticate(db: Session, header: str | None) -> tuple[McpToken, User]:
    if header is None:
        raise Rejected("missing")
    scheme, _, value = header.partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value:
        raise Rejected("missing")
    if not value.startswith(PREFIX) or len(value) > MAX_PRESENTED:
        raise Rejected("invalid")
    row = db.scalar(select(McpToken).where(McpToken.token_hash == digest(value)))
    if row is None:
        raise Rejected("invalid")
    now = clock.utcnow()
    if row.revoked_at is not None:
        raise Rejected("revoked")
    if row.expires_at <= now:
        raise Rejected("expired")
    user = db.get(User, row.user_id)
    if user is None or user.status != "active":  # disabling an account ends access at once
        raise Rejected("disabled")
    if row.last_used_at is None or now - row.last_used_at > SEEN_GRANULARITY:
        row.last_used_at = now
    return row, user


class TokenLimiter:
    """Per-token sliding window. In memory: a restart only forgives a minute of calls."""

    def __init__(self, per_minute: int) -> None:
        self._limit = per_minute
        self._recent: dict[uuid.UUID, list[float]] = {}
        self._lock = threading.Lock()

    def wait(self, token_id: uuid.UUID) -> int:
        """0 when the call may proceed (and is counted); else seconds until one may."""
        t = time.monotonic()
        with self._lock:
            recent = [x for x in self._recent.get(token_id, []) if t - x < 60]
            if len(recent) >= self._limit:
                self._recent[token_id] = recent
                return max(1, int(60 - (t - recent[0])) + 1)
            recent.append(t)
            self._recent[token_id] = recent
            if len(self._recent) > 10_000:
                self._recent = {k: v for k, v in self._recent.items() if v and t - v[-1] < 60}
        return 0
