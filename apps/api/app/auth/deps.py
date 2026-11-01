"""Request-scoped auth plumbing: DB session, client IP, session cookie, CSRF, admin gate."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth import clock
from app.auth.oidc import OidcProvider
from app.auth.pin import PinService
from app.auth.security import Passwords, csrf_token_for, digest, digest_equal, origin_allowed
from app.auth.throttle import Throttle
from app.config import Settings
from app.db.models import AuthSession, User
from app.notifications.mail import Mailer

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SEEN_GRANULARITY = timedelta(seconds=60)


@dataclass
class AuthContext:
    settings: Settings
    sessions: "sessionmaker[Session]"
    passwords: Passwords
    throttle: Throttle
    mailer: Mailer
    oidc: OidcProvider
    pins: PinService

    @property
    def cookie_name(self) -> str:
        # __Host- pins the cookie to this exact host over HTTPS (no Domain, Path=/, Secure).
        return "__Host-fc_session" if self.settings.cookie_secure else "fc_session"


def http_error(status: int, code: str, headers: dict[str, str] | None = None) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code}, headers=headers)


def ctx_of(request: Request) -> AuthContext:
    ctx: AuthContext = request.app.state.auth
    return ctx


def get_db(request: Request) -> Iterator[Session]:
    with ctx_of(request).sessions() as db:
        yield db


def client_ip(request: Request) -> str:
    hops = ctx_of(request).settings.trusted_proxy_hops
    peer = request.client.host if request.client else "unknown"
    if hops > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")]
        parts = [p for p in parts if p]
        if len(parts) >= hops:
            return parts[-hops]
    return peer


def check_origin(request: Request) -> None:
    """Cross-site browser requests are refused outright; non-browser clients send no Origin."""
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise http_error(403, "CROSS_SITE_REQUEST")
    origin = request.headers.get("origin")
    if origin is not None and not origin_allowed(
        origin, request.headers.get("host"), ctx_of(request).settings.extra_origins
    ):
        raise http_error(403, "ORIGIN_NOT_ALLOWED")


@dataclass
class Principal:
    user: User
    session: AuthSession

    @property
    def csrf_token(self) -> str:
        return csrf_token_for(self.session.token_hash)


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    s = ctx_of(request).settings
    response.set_cookie(
        ctx_of(request).cookie_name,
        token,
        max_age=s.session_absolute_seconds,
        path="/",
        secure=s.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookie(request: Request, response: Response) -> None:
    s = ctx_of(request).settings
    response.delete_cookie(
        ctx_of(request).cookie_name,
        path="/",
        secure=s.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def presented_session_hash(request: Request) -> str | None:
    raw = request.cookies.get(ctx_of(request).cookie_name)
    return digest(raw) if raw and len(raw) <= 128 else None


def _lookup_session(request: Request, db: Session) -> Principal | None:
    ctx = ctx_of(request)
    token_hash = presented_session_hash(request)
    if token_hash is None:
        return None
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash))
    if sess is None or sess.revoked_at is not None:
        return None
    now = clock.utcnow()
    idle = timedelta(seconds=ctx.settings.session_idle_seconds)
    if sess.expires_at <= now or sess.last_seen_at + idle <= now:
        return None
    user = db.get(User, sess.user_id)
    if user is None or user.status != "active":  # disabling an account ends access at once
        return None
    if now - sess.last_seen_at > SEEN_GRANULARITY:
        sess.last_seen_at = now
        db.commit()
    request.state.session_id = sess.id
    return Principal(user, sess)


def principal_even_if_locked(request: Request, db: Session = Depends(get_db)) -> Principal:
    """The session behind the request, including a session on the lock screen. Only the routes
    that must work there (session info, screen unlock, sign-out) use this one."""
    principal = _lookup_session(request, db)
    if principal is None:
        raise http_error(401, "UNAUTHENTICATED")
    if request.method not in SAFE_METHODS:
        check_origin(request)
        presented = request.headers.get("x-csrf-token", "")
        if not digest_equal(presented, principal.csrf_token):
            raise http_error(403, "CSRF_TOKEN_INVALID")
    return principal


def current_principal(principal: Principal = Depends(principal_even_if_locked)) -> Principal:
    """Every other route: a session on the lock screen is refused (M12b)."""
    if principal.session.locked_at is not None:
        raise http_error(423, "SESSION_LOCKED")
    return principal


def require_admin(principal: Principal = Depends(current_principal)) -> Principal:
    """Instance administration only. Role grants no content access (see 03_SECURITY_PRIVACY.md)."""
    if principal.user.role != "admin":
        raise http_error(403, "FORBIDDEN")
    return principal


def scrub_validation_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """FastAPI echoes request values in 422 bodies; that would reflect submitted passwords."""
    return [{"loc": e.get("loc"), "type": e.get("type"), "msg": e.get("msg")} for e in errors]
