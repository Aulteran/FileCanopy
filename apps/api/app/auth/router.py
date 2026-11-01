"""Setup, login/logout, password change, and invitation/reset token redemption."""

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import bootstrap, clock, oidc_only
from app.auth.deps import (
    Principal,
    check_origin,
    clear_session_cookie,
    client_ip,
    ctx_of,
    current_principal,
    get_db,
    http_error,
    presented_session_hash,
    principal_even_if_locked,
    set_session_cookie,
)
from app.auth.schemas import (
    AcceptInvitation,
    ChangePassword,
    CompleteReset,
    ForgotPassword,
    Login,
    Ok,
    SessionOut,
    SetupAdmin,
    SetupStatus,
    UserOut,
)
from app.auth.security import PasswordPolicyError, check_password_policy
from app.auth.service import (
    audit,
    consume_token,
    create_session,
    create_user,
    drop_unused_tokens,
    issue_token,
    normalize_email,
    revoke_session_by_hash,
    revoke_sessions,
    valid_email,
)
from app.db.models import AuthToken, ConfidentialUnlock, User

router = APIRouter(prefix="/api/v1", tags=["auth"])


def _policy(password: str, email: str) -> None:
    try:
        check_password_policy(password, email)
    except PasswordPolicyError as exc:
        raise http_error(422, exc.code) from exc


def _blocked(retry: int) -> Exception:
    return http_error(429, "TOO_MANY_ATTEMPTS", {"Retry-After": str(retry)})


def _name(raw: str) -> str:
    name = raw.strip()
    if not name or any(ord(c) < 32 for c in name):
        raise http_error(422, "INVALID_NAME")
    return name


@router.get("/setup/status", response_model=SetupStatus)
def setup_status(request: Request, db: Session = Depends(get_db)) -> SetupStatus:
    required = bootstrap.setup_required(db)
    if required and not getattr(request.app.state, "setup_token_issued", False):
        ctx = ctx_of(request)
        bootstrap.ensure_token(ctx.sessions, ctx.settings)  # DB was down at startup
        request.app.state.setup_token_issued = True
    return SetupStatus(setup_required=required)


@router.post("/setup/admin", response_model=UserOut, status_code=201)
def setup_admin(body: SetupAdmin, request: Request, db: Session = Depends(get_db)) -> UserOut:
    ctx, ip = ctx_of(request), client_ip(request)
    check_origin(request)
    keys = ctx.throttle.ip_keys("setup", ip)
    if (retry := ctx.throttle.retry_after(db, keys)) is not None:
        db.commit()
        raise _blocked(retry)
    if not bootstrap.setup_required(db):
        raise http_error(409, "SETUP_ALREADY_COMPLETE")
    if not bootstrap.token_valid(db, ctx.settings, body.setup_token):
        ctx.throttle.record_failure(db, keys)
        audit(db, "setup.bad_token", ip=ip)
        db.commit()
        raise http_error(403, "INVALID_SETUP_TOKEN")
    email = normalize_email(body.email)
    if not valid_email(email):
        raise http_error(422, "INVALID_EMAIL")
    _policy(body.password, email)
    name = _name(body.name)
    try:
        bootstrap.mark_complete(db)
        user = create_user(
            db,
            email=email,
            name=name,
            role="admin",
            status="active",
            password_hash=ctx.passwords.hash(body.password),
        )
        audit(db, "setup.admin_created", actor=user.id, target=user.id, ip=ip)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise http_error(409, "SETUP_ALREADY_COMPLETE") from exc
    return UserOut.of(user)


@router.post("/auth/login", response_model=SessionOut)
def login(
    body: Login, request: Request, response: Response, db: Session = Depends(get_db)
) -> SessionOut:
    ctx, ip = ctx_of(request), client_ip(request)
    check_origin(request)
    oidc_only.require_password_login(db, ctx.settings)  # answers before looking at the account
    email = normalize_email(body.email)
    keys = ctx.throttle.login_keys(email, ip)
    if (retry := ctx.throttle.retry_after(db, keys)) is not None:
        audit(db, "login.throttled", ip=ip)
        db.commit()
        raise _blocked(retry)
    user = db.scalar(select(User).where(User.email == email))
    stored = user.password_hash if user is not None and user.status == "active" else None
    # Always runs one Argon2 verification (dummy hash when no usable account): uniform timing.
    verified = ctx.passwords.verify(stored, body.password)
    if user is None or stored is None or not verified:
        ctx.throttle.record_failure(db, keys)
        audit(db, "login.failed", target=user.id if user else None, ip=ip)
        db.commit()
        raise http_error(401, "INVALID_CREDENTIALS")
    # Only the narrow account+IP window resets: success must not erase distributed-guess history.
    ctx.throttle.clear(db, keys[:1])
    if ctx.passwords.needs_rehash(stored):
        user.password_hash = ctx.passwords.hash(body.password)
    if old := presented_session_hash(request):  # a presented session never survives a login
        revoke_session_by_hash(db, old)
    token, csrf = create_session(
        db, user, ctx.settings.session_absolute_seconds, request.headers.get("user-agent")
    )
    audit(db, "login.success", actor=user.id, target=user.id, ip=ip)
    db.commit()
    set_session_cookie(request, response, token)
    return SessionOut(user=UserOut.of(user), csrf_token=csrf)


@router.get("/auth/session", response_model=SessionOut)
def session_info(
    request: Request,
    principal: Principal = Depends(principal_even_if_locked),
    db: Session = Depends(get_db),
) -> SessionOut:
    return SessionOut(
        user=UserOut.of(principal.user),
        csrf_token=principal.csrf_token,
        locked=principal.session.locked_at is not None,
        pin_set=ctx_of(request).pins.is_set(db, principal.user.id),
    )


@router.post("/auth/logout", response_model=Ok)
def logout(
    request: Request,
    response: Response,
    principal: Principal = Depends(principal_even_if_locked),
    db: Session = Depends(get_db),
) -> Ok:
    principal.session.revoked_at = clock.utcnow()
    db.execute(
        delete(ConfidentialUnlock).where(ConfidentialUnlock.session_id == principal.session.id)
    )
    audit(db, "logout", actor=principal.user.id, ip=client_ip(request))
    db.commit()
    clear_session_cookie(request, response)
    return Ok()


@router.post("/auth/password", response_model=SessionOut)
def change_password(
    body: ChangePassword,
    request: Request,
    response: Response,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> SessionOut:
    ctx, ip, user = ctx_of(request), client_ip(request), principal.user
    keys = ctx.throttle.login_keys(user.email, ip)
    if (retry := ctx.throttle.retry_after(db, keys)) is not None:
        raise _blocked(retry)
    if not ctx.passwords.verify(user.password_hash, body.current_password):
        ctx.throttle.record_failure(db, keys)
        audit(db, "password.change_failed", actor=user.id, target=user.id, ip=ip)
        db.commit()
        raise http_error(403, "INVALID_CREDENTIALS")
    _policy(body.new_password, user.email)
    user.password_hash = ctx.passwords.hash(body.new_password)
    user.password_changed_at = clock.utcnow()
    # Every session ends, then a fresh one is issued here (rotation on privilege change).
    revoke_sessions(db, user.id)
    token, csrf = create_session(
        db, user, ctx.settings.session_absolute_seconds, request.headers.get("user-agent")
    )
    audit(db, "password.changed", actor=user.id, target=user.id, ip=ip)
    db.commit()
    set_session_cookie(request, response, token)
    return SessionOut(user=UserOut.of(user), csrf_token=csrf)


def _redeem(db: Session, request: Request, token: str, purpose: str, expected_status: str) -> User:
    ctx, ip = ctx_of(request), client_ip(request)
    keys = ctx.throttle.ip_keys("token", ip)
    if (retry := ctx.throttle.retry_after(db, keys)) is not None:
        raise _blocked(retry)
    user = consume_token(db, token, purpose)
    if user is None or user.status != expected_status:
        db.rollback()  # a token for an ineligible account is not burned
        ctx.throttle.record_failure(db, keys)
        db.commit()
        raise http_error(400, "INVALID_TOKEN")  # unknown, expired, replayed: indistinguishable
    return user


@router.post("/auth/invitations/accept", response_model=Ok)
def accept_invitation(
    body: AcceptInvitation, request: Request, db: Session = Depends(get_db)
) -> Ok:
    ctx = ctx_of(request)
    oidc_only.require_password_login(db, ctx.settings)
    user = _redeem(db, request, body.token, "invitation", "invited")
    _policy(body.password, user.email)  # raising here rolls back, so the token is not consumed
    user.password_hash = ctx.passwords.hash(body.password)
    user.password_changed_at = clock.utcnow()
    user.status = "active"
    if body.name is not None:
        user.name = _name(body.name)
    drop_unused_tokens(db, user.id)
    audit(db, "invitation.accepted", actor=user.id, target=user.id, ip=client_ip(request))
    db.commit()
    return Ok()


@router.post("/auth/password-reset/complete", response_model=Ok)
def complete_reset(body: CompleteReset, request: Request, db: Session = Depends(get_db)) -> Ok:
    ctx = ctx_of(request)
    oidc_only.require_password_login(db, ctx.settings)
    user = _redeem(db, request, body.token, "reset", "active")
    _policy(body.password, user.email)
    user.password_hash = ctx.passwords.hash(body.password)
    user.password_changed_at = clock.utcnow()
    revoke_sessions(db, user.id)
    drop_unused_tokens(db, user.id)
    audit(db, "password.reset_completed", actor=user.id, target=user.id, ip=client_ip(request))
    db.commit()
    return Ok()


RESET_COOLDOWN_SECONDS = 60


@router.post("/auth/password-reset/request", status_code=202, response_model=Ok)
def request_reset(body: ForgotPassword, request: Request, db: Session = Depends(get_db)) -> Ok:
    """Self-service "forgot password", only when mail is configured. The answer is the same for
    every address (known, unknown, disabled, never activated) and the mail goes out from a
    background thread, so neither the reply nor its timing says whether an account exists. Every
    request counts against per-address and per-IP limits."""
    ctx, ip = ctx_of(request), client_ip(request)
    check_origin(request)
    oidc_only.require_password_login(db, ctx.settings)
    if not ctx.mailer.configured:
        raise http_error(409, "EMAIL_NOT_CONFIGURED")
    email = normalize_email(body.email)
    keys = ctx.throttle.reset_request_keys(email, ip)
    if (retry := ctx.throttle.retry_after(db, keys)) is not None:
        db.commit()
        raise _blocked(retry)
    ctx.throttle.record_failure(db, keys)
    user = db.scalar(select(User).where(User.email == email)) if valid_email(email) else None
    message: tuple[str, str, str] | None = None
    if user is not None and user.status == "active" and user.password_hash:
        recent = db.scalar(
            select(AuthToken).where(
                AuthToken.user_id == user.id,
                AuthToken.purpose == "reset",
                AuthToken.created_at > clock.utcnow() - timedelta(seconds=RESET_COOLDOWN_SECONDS),
            )
        )
        if recent is None:
            token, _ = issue_token(db, user, "reset", ctx.settings.reset_ttl_seconds, None)
            audit(db, "password.reset_requested", target=user.id, ip=ip)
            message = ctx.mailer.reset(user.email, token)
    db.commit()
    if message is not None:
        ctx.mailer.send_in_background(*message)
    return Ok()
