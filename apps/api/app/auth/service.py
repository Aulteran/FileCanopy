"""Account, session and token operations. Callers own the transaction (explicit commit)."""

import re
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.orm import Session

from app.auth import clock
from app.auth.security import csrf_token_for, digest, new_token
from app.db.models import AuthAudit, AuthSession, AuthToken, User
from app.domain import nodes

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")


def normalize_email(raw: str) -> str:
    return raw.strip().lower()


def valid_email(email: str) -> bool:
    return len(email) <= 254 and EMAIL_RE.match(email) is not None


def audit(
    db: Session,
    action: str,
    *,
    actor: uuid.UUID | None = None,
    target: uuid.UUID | None = None,
    ip: str | None = None,
    **details: Any,
) -> None:
    """Never pass secrets, tokens, passwords or plaintext emails in `details`."""
    db.add(
        AuthAudit(
            at=clock.utcnow(),
            action=action,
            actor_user_id=actor,
            target_user_id=target,
            ip=ip,
            details=details,
        )
    )


def create_user(
    db: Session, *, email: str, name: str, role: str, status: str, password_hash: str | None
) -> User:
    now = clock.utcnow()
    user = User(
        email=email,
        name=name,
        role=role,
        status=status,
        password_hash=password_hash,
        created_at=now,
        password_changed_at=now if password_hash else None,
    )
    db.add(user)
    db.flush()
    nodes.provision_user_storage(db, user)  # personal root + default shared grant, same transaction
    return user


def issue_token(
    db: Session, user: User, purpose: str, ttl_seconds: int, created_by: uuid.UUID | None
) -> tuple[str, AuthToken]:
    """Returns the plaintext token (shown once, never stored). Replaces older unused tokens."""
    db.execute(
        delete(AuthToken).where(
            AuthToken.user_id == user.id, AuthToken.purpose == purpose, AuthToken.used_at.is_(None)
        )
    )
    token = new_token()
    now = clock.utcnow()
    row = AuthToken(
        user_id=user.id,
        purpose=purpose,
        token_hash=digest(token),
        created_by=created_by,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    db.add(row)
    db.flush()
    return token, row


def consume_token(db: Session, token: str, purpose: str) -> User | None:
    """Atomically marks the token used. Unknown, expired, replayed and wrong-purpose tokens all
    yield None; the conditional UPDATE makes concurrent replays race to a single winner."""
    row = db.scalar(
        select(AuthToken).where(AuthToken.token_hash == digest(token), AuthToken.purpose == purpose)
    )
    if row is None:
        return None
    now = clock.utcnow()
    won: CursorResult[Any] = db.execute(  # type: ignore[assignment]
        update(AuthToken)
        .where(AuthToken.id == row.id, AuthToken.used_at.is_(None), AuthToken.expires_at > now)
        .values(used_at=now)
    )
    if won.rowcount != 1:
        return None
    return db.get(User, row.user_id)


def drop_unused_tokens(db: Session, user_id: uuid.UUID) -> None:
    db.execute(delete(AuthToken).where(AuthToken.user_id == user_id, AuthToken.used_at.is_(None)))


def create_session(
    db: Session, user: User, absolute_seconds: int, user_agent: str | None
) -> tuple[str, str]:
    """Returns (cookie token, csrf token). Always a fresh token: never reuses a presented one."""
    token = new_token()
    now = clock.utcnow()
    db.add(
        AuthSession(
            token_hash=digest(token),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(seconds=absolute_seconds),
            user_agent=(user_agent or "")[:200] or None,
        )
    )
    db.flush()
    return token, csrf_token_for(digest(token))


def revoke_sessions(db: Session, user_id: uuid.UUID, except_hash: str | None = None) -> int:
    stmt = (
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=clock.utcnow())
    )
    if except_hash:
        stmt = stmt.where(AuthSession.token_hash != except_hash)
    result: CursorResult[Any] = db.execute(stmt)  # type: ignore[assignment]
    return int(result.rowcount)


def revoke_session_by_hash(db: Session, token_hash: str) -> None:
    db.execute(
        update(AuthSession)
        .where(AuthSession.token_hash == token_hash, AuthSession.revoked_at.is_(None))
        .values(revoked_at=clock.utcnow())
    )
