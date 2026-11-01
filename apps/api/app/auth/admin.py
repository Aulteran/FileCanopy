"""Admin user-management API. Instance administration only: it exposes accounts, never password
hashes, plaintext passwords or session tokens, and grants no access to anyone's content."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import CursorResult, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import oidc_only
from app.auth.deps import Principal, client_ip, ctx_of, get_db, http_error, require_admin
from app.auth.schemas import (
    AdminUserOut,
    CreateUser,
    IssuedToken,
    Ok,
    SendOptions,
    UpdateUser,
    UserOut,
    UserWithToken,
)
from app.auth.service import (
    audit,
    create_user,
    drop_unused_tokens,
    issue_token,
    normalize_email,
    revoke_sessions,
    valid_email,
)
from app.db.models import AuthToken, User, UserEmailAlias, UserIdentity
from app.mail.address import address_forms, normalize_address, reserved_addresses

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _get_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise http_error(404, "NOT_FOUND")
    return user


def _deliver(
    request: Request,
    db: Session,
    user: User,
    token: str,
    row: AuthToken,
    send_email: bool,
) -> IssuedToken:
    """Commits, then (if asked) emails the link. A link that was emailed is not returned; if the
    mail fails the link is returned so the administrator can hand it over."""
    purpose, expires = row.purpose, row.expires_at
    delivery = "manual"
    if send_email:
        mailer = ctx_of(request).mailer
        db.commit()
        message = (
            mailer.invitation(user.email, user.name, token)
            if purpose == "invitation"
            else mailer.reset(user.email, token)
        )
        delivery = "email_sent" if mailer.send(*message) else "email_failed"
    return IssuedToken(
        token=None if delivery == "email_sent" else token,
        purpose=purpose,
        expires_at=expires,
        delivery=delivery,  # type: ignore[arg-type]
    )


def _require_mail(request: Request, wanted: bool) -> None:
    if wanted and not ctx_of(request).mailer.configured:
        raise http_error(409, "EMAIL_NOT_CONFIGURED")


def _guard_last_admin(db: Session, target: User) -> None:
    """Refuse changes that would leave no active administrator. Rows are locked (FOR UPDATE on
    PostgreSQL) so two admins cannot demote each other concurrently."""
    admins = db.scalars(
        select(User).where(User.role == "admin", User.status == "active").with_for_update()
    ).all()
    if all(a.id == target.id for a in admins):
        raise http_error(409, "LAST_ADMIN")


@router.get("/users", response_model=list[AdminUserOut])
def list_users(
    _: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> list[AdminUserOut]:
    linked = set(db.scalars(select(UserIdentity.user_id)))
    return [
        AdminUserOut(**UserOut.of(u).model_dump(), sso_linked=u.id in linked)
        for u in db.scalars(select(User).order_by(User.created_at, User.email))
    ]


@router.post("/users", response_model=UserWithToken, status_code=201)
def create_invited_user(
    body: CreateUser,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserWithToken:
    ctx = ctx_of(request)
    _require_mail(request, body.send_email)
    email = normalize_email(body.email)
    if not valid_email(email):
        raise http_error(422, "INVALID_EMAIL")
    name = body.name.strip()
    if not name or any(ord(c) < 32 for c in name):
        raise http_error(422, "INVALID_NAME")
    # A verified sender alias is an identity too (M13): one address, one account.
    mail_address = normalize_address(email)
    if mail_address is not None and mail_address in reserved_addresses(ctx.settings):
        raise http_error(409, "EMAIL_EXISTS")  # the intake or system address (review L2)
    if mail_address is not None and db.scalar(
        select(User.id).where(User.email.in_(address_forms(mail_address)))
    ):
        raise http_error(409, "EMAIL_EXISTS")  # same mailbox under another spelling (review L3)
    if db.scalar(
        select(UserEmailAlias.id).where(
            UserEmailAlias.email.in_(address_forms(mail_address or email)),
            UserEmailAlias.verified_at.is_not(None),
        )
    ):
        raise http_error(409, "EMAIL_EXISTS")
    try:
        user = create_user(
            db, email=email, name=name, role=body.role, status="invited", password_hash=None
        )
    except IntegrityError as exc:
        db.rollback()
        raise http_error(409, "EMAIL_EXISTS") from exc
    token, row = issue_token(
        db, user, "invitation", ctx.settings.invitation_ttl_seconds, admin.user.id
    )
    audit(
        db,
        "user.invited",
        actor=admin.user.id,
        target=user.id,
        ip=client_ip(request),
        role=user.role,
        email=body.send_email,
    )
    out = _deliver(request, db, user, token, row, body.send_email)
    db.commit()
    return UserWithToken(user=UserOut.of(user), token=out)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    body: UpdateUser,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserOut:
    user = _get_user(db, user_id)
    changes: dict[str, str] = {}
    demoting = body.role is not None and body.role != "admin" and user.role == "admin"
    disabling = body.status == "disabled" and user.status != "disabled"
    if user.status == "active" and user.role == "admin" and (demoting or disabling):
        _guard_last_admin(db, user)
        oidc_only.guard_linked_admin(db, ctx_of(request).settings, user)
    if body.name is not None and body.name.strip() != user.name:
        name = body.name.strip()
        if not name or any(ord(c) < 32 for c in name):
            raise http_error(422, "INVALID_NAME")
        user.name = name
        changes["name"] = "changed"
    if body.role is not None and body.role != user.role:
        user.role = body.role
        changes["role"] = body.role
    if body.status is not None and body.status != user.status:
        if body.status == "active" and user.status == "invited":
            raise http_error(409, "INVITATION_PENDING")
        # Re-enabling an account that never set a password returns it to the invited state.
        has_identity = db.scalar(select(UserIdentity.id).where(UserIdentity.user_id == user.id))
        user.status = (
            "invited"
            if body.status == "active" and not user.password_hash and has_identity is None
            else body.status
        )
        changes["status"] = user.status
        if user.status == "disabled":
            revoke_sessions(db, user.id)
            drop_unused_tokens(db, user.id)
    if changes:
        audit(
            db,
            "user.updated",
            actor=admin.user.id,
            target=user.id,
            ip=client_ip(request),
            **changes,
        )
    db.commit()
    return UserOut.of(user)


@router.post("/users/{user_id}/invitation", response_model=IssuedToken)
def reissue_invitation(
    user_id: uuid.UUID,
    request: Request,
    options: SendOptions | None = None,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> IssuedToken:
    send = options.send_email if options else False
    _require_mail(request, send)
    user = _get_user(db, user_id)
    settings = ctx_of(request).settings
    # In provider-only mode an active person without a link gets a link invitation: it can only
    # be accepted through the provider (no password involved) and links that provider account.
    link_only = (
        user.status == "active"
        and oidc_only.effective(db, settings)
        and oidc_only.is_unlinked(db, settings, user)
    )
    if user.status != "invited" and not link_only:
        raise http_error(409, "NOT_INVITED")
    token, row = issue_token(
        db, user, "invitation", ctx_of(request).settings.invitation_ttl_seconds, admin.user.id
    )
    audit(
        db,
        "user.invitation_reissued",
        actor=admin.user.id,
        target=user.id,
        ip=client_ip(request),
        email=send,
    )
    out = _deliver(request, db, user, token, row, send)
    db.commit()
    return out


@router.post("/users/{user_id}/password-reset", response_model=IssuedToken)
def issue_password_reset(
    user_id: uuid.UUID,
    request: Request,
    options: SendOptions | None = None,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> IssuedToken:
    """Issues a one-time reset token; the old password is never visible. The user's sessions
    stay valid until the reset is completed (or an admin revokes them)."""
    send = options.send_email if options else False
    _require_mail(request, send)
    user = _get_user(db, user_id)
    oidc_only.require_password_login(db, ctx_of(request).settings)  # a reset would be unusable
    if user.status != "active":
        raise http_error(409, "NOT_ACTIVE")
    token, row = issue_token(
        db, user, "reset", ctx_of(request).settings.reset_ttl_seconds, admin.user.id
    )
    audit(
        db,
        "user.reset_issued",
        actor=admin.user.id,
        target=user.id,
        ip=client_ip(request),
        email=send,
    )
    out = _deliver(request, db, user, token, row, send)
    db.commit()
    return out


@router.post("/users/{user_id}/revoke-sessions", response_model=Ok)
def revoke_user_sessions(
    user_id: uuid.UUID,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Ok:
    user = _get_user(db, user_id)
    count = revoke_sessions(db, user.id)
    audit(
        db,
        "user.sessions_revoked",
        actor=admin.user.id,
        target=user.id,
        ip=client_ip(request),
        count=count,
    )
    db.commit()
    return Ok()


@router.get("/auth-config")
def auth_config(request: Request, _: Principal = Depends(require_admin)) -> dict[str, object]:
    """Read-only view of how sign-in and mail are configured. Never includes a secret."""
    s = ctx_of(request).settings
    return {
        "smtp": {
            "configured": s.smtp_configured,
            "host": s.smtp_host if s.smtp_configured else None,
            "from": s.smtp_from if s.smtp_configured else None,
            "security": s.smtp_security if s.smtp_configured else None,
        },
        "oidc": {
            "enabled": s.oidc_enabled,
            "issuer": s.oidc_issuer if s.oidc_enabled else None,
            "label": s.oidc_label,
            "redirect_uri": s.oidc_redirect_uri if s.oidc_enabled else None,
            "google": s.oidc_enabled and s.oidc_is_google,
            "hosted_domain": s.oidc_hosted_domain or None,
        },
    }


@router.post("/smtp/test", response_model=Ok)
def smtp_test(
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Ok:
    """Sends a test message to the administrator's own address."""
    mailer = ctx_of(request).mailer
    if not mailer.configured:
        raise http_error(409, "EMAIL_NOT_CONFIGURED")
    ok = mailer.send(*mailer.test_message(admin.user.email))
    audit(db, "smtp.test", actor=admin.user.id, ip=client_ip(request), sent=ok)
    db.commit()
    if not ok:
        raise http_error(502, "EMAIL_SEND_FAILED")
    return Ok()


@router.delete("/users/{user_id}/identity", response_model=Ok)
def unlink_identity(
    user_id: uuid.UUID,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Ok:
    """Removes the person's single sign-on link and ends their sessions. Their local password
    still works; they can be re-linked from their own account."""
    user = _get_user(db, user_id)
    oidc_only.guard_linked_admin(db, ctx_of(request).settings, user)
    removed: CursorResult[Any] = db.execute(  # type: ignore[assignment]
        delete(UserIdentity).where(UserIdentity.user_id == user.id)
    )
    if not removed.rowcount:
        raise http_error(404, "NOT_FOUND")
    revoke_sessions(db, user.id)
    audit(db, "user.identity_unlinked", actor=admin.user.id, target=user.id, ip=client_ip(request))
    db.commit()
    return Ok()
