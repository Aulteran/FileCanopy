"""OIDC sign-in, linking and invitation acceptance through a provider (M12, ADR-020).

Every flow starts with a POST that stores a single-use `oidc_login` row (state hash, nonce, PKCE
verifier, browser binding) and returns the provider URL. The callback consumes the row with one
conditional UPDATE, checks the binding cookie, exchanges the code, validates the ID token and then
acts by purpose. The provider only ever proves *who* someone is at the provider; which account
that is comes from a stored (issuer, subject) link, never from an email address.
"""

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import clock
from app.auth.deps import (
    Principal,
    check_origin,
    client_ip,
    ctx_of,
    current_principal,
    get_db,
    http_error,
    presented_session_hash,
    set_session_cookie,
)
from app.auth.oidc import OidcError, new_pkce
from app.auth.schemas import (
    AuthConfig,
    AuthorizationUrl,
    IdentityOut,
    OidcPublic,
    OidcStartInvite,
    OidcStartLink,
    Ok,
)
from app.auth.security import (
    PasswordPolicyError,
    check_password_policy,
    digest,
    digest_equal,
    new_token,
)
from app.auth.service import (
    audit,
    consume_token_hash,
    create_session,
    drop_unused_tokens,
    revoke_session_by_hash,
    token_usable,
)
from app.db.models import AuthSession, OidcLogin, User, UserIdentity

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

LOGIN_TTL = timedelta(minutes=10)


def _require_enabled(request: Request) -> None:
    if not ctx_of(request).settings.oidc_enabled:
        raise http_error(404, "OIDC_DISABLED")


def _binding_cookie(request: Request) -> str:
    return "__Host-fc_oidc" if ctx_of(request).settings.cookie_secure else "fc_oidc"


def _blocked(retry: int) -> Exception:
    return http_error(429, "TOO_MANY_ATTEMPTS", {"Retry-After": str(retry)})


@router.get("/config", response_model=AuthConfig)
def auth_config(request: Request) -> AuthConfig:
    """Public: what the sign-in page may offer. No secrets, no hosts."""
    s = ctx_of(request).settings
    return AuthConfig(
        oidc=OidcPublic(enabled=s.oidc_enabled, label=s.oidc_label),
        password_reset_by_email=s.smtp_configured,
    )


def _start(
    request: Request,
    response: Response,
    db: Session,
    purpose: str,
    *,
    user_id: Any = None,
    session_id: Any = None,
    token_hash: str | None = None,
    password_hash: str | None = None,
    new_name: str | None = None,
) -> AuthorizationUrl:
    ctx = ctx_of(request)
    verifier, challenge = new_pkce()
    state, binding, nonce = new_token(), new_token(), new_token()
    # Contact the provider first: if it is down, nothing is stored.
    url = ctx.oidc.authorization_url(state, nonce, challenge)
    now = clock.utcnow()
    db.execute(delete(OidcLogin).where(OidcLogin.expires_at < now - timedelta(hours=1)))
    db.add(
        OidcLogin(
            state_hash=digest(state),
            binding_hash=digest(binding),
            nonce=nonce,
            code_verifier=verifier,
            purpose=purpose,
            user_id=user_id,
            session_id=session_id,
            token_hash=token_hash,
            password_hash=password_hash,
            new_name=new_name,
            created_at=now,
            expires_at=now + LOGIN_TTL,
        )
    )
    db.commit()
    response.set_cookie(
        _binding_cookie(request),
        binding,
        max_age=int(LOGIN_TTL.total_seconds()),
        path="/",
        secure=ctx.settings.cookie_secure,
        httponly=True,
        samesite="lax",  # the provider's redirect back is a cross-site top-level navigation
    )
    return AuthorizationUrl(authorization_url=url)


def _throttled_start(request: Request, db: Session) -> None:
    ctx = ctx_of(request)
    keys = ctx.throttle.ip_keys("oidc-start", client_ip(request))
    if (retry := ctx.throttle.retry_after(db, keys)) is not None:
        db.commit()
        raise _blocked(retry)
    ctx.throttle.record_failure(db, keys)  # every start counts: starting is not free for us
    db.commit()  # kept even if the provider is down and the start is rolled back


def _provider_call(db: Session, fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except OidcError as exc:
        db.rollback()
        raise http_error(503, exc.code) from exc


@router.post("/oidc/login/start", response_model=AuthorizationUrl)
def login_start(request: Request, response: Response, db: Session = Depends(get_db)) -> Any:
    _require_enabled(request)
    check_origin(request)
    _throttled_start(request, db)
    return _provider_call(db, _start, request, response, db, "login")


@router.post("/oidc/link/start", response_model=AuthorizationUrl)
def link_start(
    body: OidcStartLink,
    request: Request,
    response: Response,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> Any:
    _require_enabled(request)
    ctx, ip, user = ctx_of(request), client_ip(request), principal.user
    keys = ctx.throttle.login_keys(user.email, ip)
    if (retry := ctx.throttle.retry_after(db, keys)) is not None:
        raise _blocked(retry)
    if not ctx.passwords.verify(user.password_hash, body.password):
        ctx.throttle.record_failure(db, keys)
        audit(db, "oidc.link_password_failed", actor=user.id, target=user.id, ip=ip)
        db.commit()
        raise http_error(403, "INVALID_CREDENTIALS")
    return _provider_call(db, _start_link, request, response, db, user.id, principal.session.id)


def _start_link(
    request: Request, response: Response, db: Session, user_id: Any, session_id: Any
) -> AuthorizationUrl:
    return _start(request, response, db, "link", user_id=user_id, session_id=session_id)


@router.post("/oidc/invitation/start", response_model=AuthorizationUrl)
def invitation_start(
    body: OidcStartInvite, request: Request, response: Response, db: Session = Depends(get_db)
) -> Any:
    """Checks the invitation and the chosen password now, so a mistake is reported before the
    person leaves for the provider. The token is only *consumed* by the callback."""
    _require_enabled(request)
    check_origin(request)
    ctx, ip = ctx_of(request), client_ip(request)
    keys = ctx.throttle.ip_keys("token", ip)
    if (retry := ctx.throttle.retry_after(db, keys)) is not None:
        raise _blocked(retry)
    user = token_usable(db, body.token, "invitation")
    if user is None or user.status != "invited":
        ctx.throttle.record_failure(db, keys)
        db.commit()
        raise http_error(400, "INVALID_TOKEN")
    try:
        check_password_policy(body.password, user.email)
    except PasswordPolicyError as exc:
        raise http_error(422, exc.code) from exc
    name = None
    if body.name is not None:
        name = body.name.strip()
        if not name or any(ord(c) < 32 for c in name):
            raise http_error(422, "INVALID_NAME")
    return _provider_call(
        db,
        _start_invite,
        request,
        response,
        db,
        digest(body.token),
        ctx.passwords.hash(body.password),
        name,
    )


def _start_invite(
    request: Request,
    response: Response,
    db: Session,
    token_hash: str,
    password_hash: str,
    name: str | None,
) -> AuthorizationUrl:
    return _start(
        request,
        response,
        db,
        "invite",
        token_hash=token_hash,
        password_hash=password_hash,
        new_name=name,
    )


# ------------------------------------------------------------------ callback


def _redirect(request: Request, code: str, session_token: str | None = None) -> Response:
    resp = RedirectResponse(url=f"/?sso={code}", status_code=303)
    resp.delete_cookie(
        _binding_cookie(request),
        path="/",
        secure=ctx_of(request).settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    if session_token is not None:
        set_session_cookie(request, resp, session_token)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _fail(request: Request, db: Session, code: str, row: OidcLogin | None = None) -> Response:
    db.rollback()
    ctx, ip = ctx_of(request), client_ip(request)
    ctx.throttle.record_failure(db, ctx.throttle.ip_keys("oidc-cb", ip))
    audit(db, "oidc.failed", ip=ip, reason=code, purpose=row.purpose if row else None)
    db.commit()
    return _redirect(request, code)


def _link_identity(db: Session, user: User, issuer: str, sub: str, email: str | None) -> None:
    taken = db.scalar(
        select(UserIdentity).where(UserIdentity.issuer == issuer, UserIdentity.subject == sub)
    )
    if taken is not None:
        raise OidcError("IDENTITY_IN_USE")
    if db.scalar(
        select(UserIdentity).where(UserIdentity.user_id == user.id, UserIdentity.issuer == issuer)
    ):
        raise OidcError("IDENTITY_IN_USE")
    now = clock.utcnow()
    db.add(
        UserIdentity(
            user_id=user.id,
            issuer=issuer,
            subject=sub,
            email_seen=email[:254] if email else None,
            created_at=now,
            last_login_at=now,
        )
    )
    try:
        db.flush()
    except IntegrityError as exc:  # a concurrent link won the unique index
        raise OidcError("IDENTITY_IN_USE") from exc


@router.get("/oidc/callback")
def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    ctx = ctx_of(request)
    if not ctx.settings.oidc_enabled:
        return _redirect(request, "OIDC_DISABLED")
    ip = client_ip(request)
    keys = ctx.throttle.ip_keys("oidc-cb", ip)
    if ctx.throttle.retry_after(db, keys) is not None:
        db.commit()
        return _redirect(request, "TOO_MANY_ATTEMPTS")
    if not state or len(state) > 256:
        return _fail(request, db, "OIDC_STATE_INVALID")
    row = db.scalar(select(OidcLogin).where(OidcLogin.state_hash == digest(state)))
    if row is None:
        return _fail(request, db, "OIDC_STATE_INVALID")
    now = clock.utcnow()
    won: CursorResult[Any] = db.execute(  # type: ignore[assignment]
        update(OidcLogin)
        .where(OidcLogin.id == row.id, OidcLogin.used_at.is_(None), OidcLogin.expires_at > now)
        .values(used_at=now)
    )
    db.commit()  # the state is spent from here on, whatever happens next
    if won.rowcount != 1:
        return _fail(request, db, "OIDC_STATE_INVALID", row)
    binding = request.cookies.get(_binding_cookie(request), "")
    if not binding or len(binding) > 128 or not digest_equal(digest(binding), row.binding_hash):
        return _fail(request, db, "OIDC_STATE_INVALID", row)
    if error or not code or len(code) > 4096:
        return _fail(request, db, "OIDC_DENIED", row)
    token: str | None
    try:
        tokens = ctx.oidc.exchange_code(code, row.code_verifier)
        claims = ctx.oidc.verify_id_token(tokens.id_token, row.nonce)
        issuer, sub = ctx.settings.oidc_issuer, str(claims["sub"])
        if row.purpose == "login":
            outcome, token = _finish_login(request, db, issuer, sub)
        elif row.purpose == "link":
            outcome, token = _finish_link(request, db, row, issuer, sub, claims)
        elif row.purpose == "invite":
            email = ctx.oidc.verified_email(claims, tokens.access_token)
            outcome, token = _finish_invite(request, db, row, issuer, sub, email)
        else:
            raise OidcError("OIDC_STATE_INVALID")
    except OidcError as exc:
        return _fail(request, db, exc.code, row)
    ctx.throttle.clear(db, keys + ctx.throttle.ip_keys("oidc-start", client_ip(request)))
    db.commit()
    return _redirect(request, outcome, token)


def _finish_login(request: Request, db: Session, issuer: str, sub: str) -> tuple[str, str]:
    ctx = ctx_of(request)
    ident = db.scalar(
        select(UserIdentity).where(UserIdentity.issuer == issuer, UserIdentity.subject == sub)
    )
    user = db.get(User, ident.user_id) if ident else None
    if ident is None or user is None or user.status != "active":
        raise OidcError("OIDC_NOT_LINKED")  # nothing is created; email is never consulted
    ident.last_login_at = clock.utcnow()
    if old := presented_session_hash(request):
        revoke_session_by_hash(db, old)
    token, _ = create_session(
        db, user, ctx.settings.session_absolute_seconds, request.headers.get("user-agent")
    )
    audit(db, "login.success", actor=user.id, target=user.id, ip=client_ip(request), method="oidc")
    return "signed_in", token


def _finish_link(
    request: Request,
    db: Session,
    row: OidcLogin,
    issuer: str,
    sub: str,
    claims: dict[str, Any],
) -> tuple[str, str | None]:
    sess = db.get(AuthSession, row.session_id) if row.session_id else None
    user = db.get(User, row.user_id) if row.user_id else None
    now = clock.utcnow()
    if (
        sess is None
        or user is None
        or sess.user_id != user.id
        or sess.revoked_at is not None
        or sess.expires_at <= now
        or user.status != "active"
    ):
        raise OidcError("OIDC_STATE_INVALID")
    email = claims.get("email") if isinstance(claims.get("email"), str) else None
    _link_identity(db, user, issuer, sub, email)
    audit(db, "oidc.linked", actor=user.id, target=user.id, ip=client_ip(request))
    return "linked", None


def _finish_invite(
    request: Request,
    db: Session,
    row: OidcLogin,
    issuer: str,
    sub: str,
    email: str | None,
) -> tuple[str, str]:
    ctx = ctx_of(request)
    if row.token_hash is None or row.password_hash is None:
        raise OidcError("OIDC_STATE_INVALID")
    user = consume_token_hash(db, row.token_hash, "invitation")
    if user is None or user.status != "invited":
        raise OidcError("INVALID_TOKEN")
    if email is None or email != user.email:
        raise OidcError("OIDC_EMAIL_MISMATCH")  # unverified, missing or a different address
    _link_identity(db, user, issuer, sub, email)
    user.password_hash = row.password_hash
    user.password_changed_at = clock.utcnow()
    user.status = "active"
    if row.new_name:
        user.name = row.new_name
    drop_unused_tokens(db, user.id)
    if old := presented_session_hash(request):
        revoke_session_by_hash(db, old)
    token, _ = create_session(
        db, user, ctx.settings.session_absolute_seconds, request.headers.get("user-agent")
    )
    audit(db, "invitation.accepted", actor=user.id, target=user.id, ip=client_ip(request), sso=True)
    return "welcome", token


# ------------------------------------------------------------ identity status


@router.get("/oidc/identity", response_model=IdentityOut)
def identity(
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> IdentityOut:
    s = ctx_of(request).settings
    if not s.oidc_enabled:
        return IdentityOut(linked=False)
    row = db.scalar(
        select(UserIdentity).where(
            UserIdentity.user_id == principal.user.id, UserIdentity.issuer == s.oidc_issuer
        )
    )
    return IdentityOut(linked=row is not None, label=s.oidc_label)


@router.post("/oidc/unlink", response_model=Ok)
def unlink(
    body: OidcStartLink,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> Ok:
    ctx, ip, user = ctx_of(request), client_ip(request), principal.user
    keys = ctx.throttle.login_keys(user.email, ip)
    if (retry := ctx.throttle.retry_after(db, keys)) is not None:
        raise _blocked(retry)
    if not ctx.passwords.verify(user.password_hash, body.password):
        ctx.throttle.record_failure(db, keys)
        audit(db, "oidc.unlink_password_failed", actor=user.id, target=user.id, ip=ip)
        db.commit()
        raise http_error(403, "INVALID_CREDENTIALS")
    removed: CursorResult[Any] = db.execute(  # type: ignore[assignment]
        delete(UserIdentity).where(UserIdentity.user_id == user.id)
    )
    if removed.rowcount:
        audit(db, "oidc.unlinked", actor=user.id, target=user.id, ip=ip)
    db.commit()
    return Ok()
