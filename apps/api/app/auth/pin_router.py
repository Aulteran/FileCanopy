"""Confidential PIN, lock screen and the proofs that guard them (M12b).

A proof is one of: the current PIN, the account password, a fresh OIDC re-authentication of this
very session (single use), or an emailed one-time token redeemed in this signed-in session. A
cookie alone never changes the PIN.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth import clock
from app.auth.deps import (
    Principal,
    client_ip,
    ctx_of,
    current_principal,
    get_db,
    http_error,
    principal_even_if_locked,
)
from app.auth.pin import SESSION_END_AT, PinError, weak_reason
from app.auth.schemas import Ok, Strict
from app.auth.service import audit, consume_token, issue_token
from app.db.models import (
    AuthAudit,
    AuthSession,
    AuthToken,
    ConfidentialUnlock,
    UserIdentity,
)

router = APIRouter(prefix="/api/v1/auth", tags=["pin"])

REAUTH_TTL = timedelta(minutes=5)
RESET_EMAIL_COOLDOWN = timedelta(seconds=60)


class PinStatus(BaseModel):
    set: bool
    disabled: bool
    blocked_for: int | None
    has_password: bool
    reauth_available: bool  # the provider can re-check this person
    email_reset_available: bool
    locked: bool


class Proof(Strict):
    """Exactly one of these."""

    current_pin: str | None = Field(default=None, max_length=32)
    password: str | None = Field(default=None, max_length=1024)
    reauth: bool = False
    email_token: str | None = Field(default=None, max_length=256)

    def kinds(self) -> list[str]:
        out = []
        if self.current_pin is not None:
            out.append("pin")
        if self.password is not None:
            out.append("password")
        if self.reauth:
            out.append("reauth")
        if self.email_token is not None:
            out.append("email")
        return out


class PinSet(Proof):
    pin: str = Field(max_length=32)


class PinOnly(Strict):
    pin: str = Field(max_length=32)


def _linked(db: Session, request: Request, principal: Principal) -> bool:
    s = ctx_of(request).settings
    if not s.oidc_enabled:
        return False
    return (
        db.scalar(
            select(UserIdentity.id).where(
                UserIdentity.user_id == principal.user.id, UserIdentity.issuer == s.oidc_issuer
            )
        )
        is not None
    )


def raise_pin_error(exc: PinError) -> Exception:
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return http_error(exc.status, exc.code, headers)


def check_pin_for_session(
    request: Request, db: Session, principal: Principal, pin: str, *, what: str = "unlock"
) -> None:
    """Verifies the PIN for this person with the account-wide counter. At the fifth wrong guess
    the session ends (as the password check in M09 did)."""
    ctx, user, ip = ctx_of(request), principal.user, client_ip(request)
    try:
        ctx.pins.verify(db, user.id, pin)
        return
    except PinError as exc:
        err = exc
    failures = getattr(err, "failures", 0)
    if err.code == "INVALID_PIN":
        audit(db, "pin.failed", actor=user.id, target=user.id, ip=ip, failures=failures, use=what)
        if failures >= SESSION_END_AT:
            sess = db.get(AuthSession, principal.session.id)
            if sess is not None:
                sess.revoked_at = clock.utcnow()
            db.execute(
                delete(ConfidentialUnlock).where(
                    ConfidentialUnlock.session_id == principal.session.id
                )
            )
            audit(db, "pin.session_ended", actor=user.id, target=user.id, ip=ip, failures=failures)
            db.commit()
            raise http_error(401, "SESSION_ENDED_TOO_MANY_UNLOCK_FAILURES") from err
        db.commit()
    elif err.code in ("PIN_BLOCKED", "PIN_DISABLED"):
        audit(db, "pin.refused", actor=user.id, target=user.id, ip=ip, reason=err.code)
        db.commit()
    raise raise_pin_error(err) from err


def check_password(request: Request, db: Session, principal: Principal, password: str) -> None:
    ctx, ip, user = ctx_of(request), client_ip(request), principal.user
    keys = ctx.throttle.login_keys(user.email, ip)
    if (retry := ctx.throttle.retry_after(db, keys)) is not None:
        audit(db, "pin.password_throttled", actor=user.id, ip=ip)
        db.commit()
        raise http_error(429, "TOO_MANY_ATTEMPTS", {"Retry-After": str(retry)})
    if not ctx.passwords.verify(user.password_hash, password):
        ctx.throttle.record_failure(db, keys)
        audit(db, "pin.password_failed", actor=user.id, target=user.id, ip=ip)
        db.flush()
        recent = db.scalar(
            select(func.count())
            .select_from(AuthAudit)
            .where(
                AuthAudit.action == "pin.password_failed",
                AuthAudit.actor_user_id == user.id,
                AuthAudit.at >= clock.utcnow() - timedelta(minutes=10),
            )
        )
        if (recent or 0) >= SESSION_END_AT:  # same rule as the Confidential password check (M09)
            principal.session.revoked_at = clock.utcnow()
            db.execute(
                delete(ConfidentialUnlock).where(
                    ConfidentialUnlock.session_id == principal.session.id
                )
            )
            audit(db, "pin.session_ended", actor=user.id, target=user.id, ip=ip, via="password")
            db.commit()
            raise http_error(401, "SESSION_ENDED_TOO_MANY_UNLOCK_FAILURES")
        db.commit()
        raise http_error(403, "INVALID_CREDENTIALS")
    ctx.throttle.clear(db, keys[:1])


def consume_reauth(
    db: Session, principal: Principal, *, ttl: timedelta = REAUTH_TTL, need_strong: bool = False
) -> bool:
    """True once if this session re-authenticated through the provider within `ttl`. With
    `need_strong` the provider must also have said the person signed in just now (`auth_time`):
    a provider that silently reuses its own session proves nothing about who is at the keyboard."""
    sess = db.get(AuthSession, principal.session.id)
    if sess is None or sess.reauth_at is None:
        return False
    fresh = sess.reauth_at + ttl > clock.utcnow() and (sess.reauth_strong or not need_strong)
    sess.reauth_at = None  # single use, fresh or not
    sess.reauth_strong = False
    return fresh


def verify_proof(
    request: Request, db: Session, principal: Principal, proof: Proof, *, allow_pin: bool = True
) -> str:
    kinds = proof.kinds()
    if len(kinds) != 1:
        raise http_error(422, "PROOF_REQUIRED")
    kind = kinds[0]
    if kind == "pin":
        if not allow_pin or proof.current_pin is None:
            raise http_error(422, "PROOF_REQUIRED")
        check_pin_for_session(request, db, principal, proof.current_pin, what="change")
    elif kind == "password":
        check_password(request, db, principal, proof.password or "")
    elif kind == "reauth":
        if not consume_reauth(db, principal, need_strong=True):
            db.commit()
            raise http_error(403, "REAUTH_REQUIRED")
    else:
        ctx = ctx_of(request)
        user = consume_token(db, proof.email_token or "", "pin_reset")
        if user is None or user.id != principal.user.id:
            db.rollback()
            keys = ctx.throttle.ip_keys("token", client_ip(request))
            ctx.throttle.record_failure(db, keys)
            db.commit()
            raise http_error(400, "INVALID_TOKEN")
    return kind


def drop_unlocks(db: Session, principal: Principal) -> None:
    """Folders locked again for every session of the person after a PIN change."""
    sessions = select(AuthSession.id).where(AuthSession.user_id == principal.user.id)
    db.execute(delete(ConfidentialUnlock).where(ConfidentialUnlock.session_id.in_(sessions)))


@router.get("/pin", response_model=PinStatus)
def pin_status(
    request: Request,
    principal: Principal = Depends(principal_even_if_locked),
    db: Session = Depends(get_db),
) -> PinStatus:
    ctx = ctx_of(request)
    row = ctx.pins.row(db, principal.user.id)
    return PinStatus(
        set=row is not None,
        disabled=bool(row and row.disabled),
        blocked_for=ctx.pins.blocked_for(row) if row else None,
        has_password=principal.user.password_hash is not None,
        reauth_available=_linked(db, request, principal),
        email_reset_available=ctx.mailer.configured,
        locked=principal.session.locked_at is not None,
    )


@router.put("/pin", response_model=Ok)
def set_pin(
    body: PinSet,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> Ok:
    ctx, user, ip = ctx_of(request), principal.user, client_ip(request)
    if (reason := weak_reason(body.pin)) is not None:
        raise http_error(422, reason)  # before a proof is spent
    had = ctx.pins.is_set(db, user.id)
    kind = verify_proof(request, db, principal, body, allow_pin=had)
    try:
        ctx.pins.set(db, user.id, body.pin)
    except PinError as exc:
        db.rollback()
        raise raise_pin_error(exc) from exc
    drop_unlocks(db, principal)
    audit(db, "pin.changed" if had else "pin.set", actor=user.id, target=user.id, ip=ip, proof=kind)
    db.commit()
    return Ok()


@router.post("/pin/remove", response_model=Ok)
def remove_pin(
    body: Proof,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> Ok:
    ctx, user = ctx_of(request), principal.user
    kind = verify_proof(request, db, principal, body)
    if ctx.pins.remove(db, user.id):
        drop_unlocks(db, principal)
        audit(db, "pin.removed", actor=user.id, target=user.id, ip=client_ip(request), proof=kind)
    db.commit()
    return Ok()


@router.post("/pin/reset-email", status_code=202, response_model=Ok)
def pin_reset_email(
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> Ok:
    ctx, user, ip = ctx_of(request), principal.user, client_ip(request)
    if not ctx.mailer.configured:
        raise http_error(409, "EMAIL_NOT_CONFIGURED")
    keys = ctx.throttle.pin_reset_keys(str(user.id), ip)
    if (retry := ctx.throttle.retry_after(db, keys)) is not None:
        db.commit()
        raise http_error(429, "TOO_MANY_ATTEMPTS", {"Retry-After": str(retry)})
    ctx.throttle.record_failure(db, keys)
    recent = db.scalar(
        select(AuthToken).where(
            AuthToken.user_id == user.id,
            AuthToken.purpose == "pin_reset",
            AuthToken.created_at > clock.utcnow() - RESET_EMAIL_COOLDOWN,
        )
    )
    message = None
    if recent is None:
        token, _ = issue_token(db, user, "pin_reset", ctx.settings.reset_ttl_seconds, None)
        audit(db, "pin.reset_requested", actor=user.id, target=user.id, ip=ip)
        message = ctx.mailer.pin_reset(user.email, token)
    db.commit()
    if message is not None:
        ctx.mailer.send_in_background(*message)
    return Ok()


@router.post("/lock-screen", response_model=Ok)
def lock_screen(
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> Ok:
    """Locks this session: signed in, but every request but unlock and sign-out is refused."""
    ctx, user = ctx_of(request), principal.user
    if not ctx.pins.is_set(db, user.id):
        raise http_error(409, "PIN_NOT_SET")
    sess = principal.session
    sess.locked_at = clock.utcnow()
    sess.reauth_at = None
    sess.reauth_strong = False
    db.execute(
        delete(ConfidentialUnlock).where(ConfidentialUnlock.session_id == principal.session.id)
    )
    audit(db, "session.locked", actor=user.id, target=user.id, ip=client_ip(request))
    db.commit()
    return Ok()


@router.post("/lock-screen/unlock", response_model=Ok)
def unlock_screen(
    body: PinOnly,
    request: Request,
    principal: Principal = Depends(principal_even_if_locked),
    db: Session = Depends(get_db),
) -> Ok:
    """Returns to the app. Confidential folders stay locked: each needs the PIN again."""
    if principal.session.locked_at is None:
        raise http_error(409, "NOT_LOCKED")
    check_pin_for_session(request, db, principal, body.pin, what="screen")
    sess = principal.session
    sess.locked_at = None
    audit(
        db,
        "session.unlocked",
        actor=principal.user.id,
        target=principal.user.id,
        ip=client_ip(request),
    )
    db.commit()
    return Ok()
