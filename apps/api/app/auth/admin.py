"""Admin user-management API. Instance administration only: it exposes accounts, never password
hashes, plaintext passwords or session tokens, and grants no access to anyone's content."""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import Principal, client_ip, ctx_of, get_db, http_error, require_admin
from app.auth.schemas import CreateUser, Ok, TokenOut, UpdateUser, UserOut, UserWithToken
from app.auth.service import (
    audit,
    create_user,
    drop_unused_tokens,
    issue_token,
    normalize_email,
    revoke_sessions,
    valid_email,
)
from app.db.models import AuthToken, User

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _get_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise http_error(404, "NOT_FOUND")
    return user


def _token_out(token: str, row: AuthToken) -> TokenOut:
    return TokenOut(token=token, purpose=row.purpose, expires_at=row.expires_at)


def _guard_last_admin(db: Session, target: User) -> None:
    """Refuse changes that would leave no active administrator. Rows are locked (FOR UPDATE on
    PostgreSQL) so two admins cannot demote each other concurrently."""
    admins = db.scalars(
        select(User).where(User.role == "admin", User.status == "active").with_for_update()
    ).all()
    if all(a.id == target.id for a in admins):
        raise http_error(409, "LAST_ADMIN")


@router.get("/users", response_model=list[UserOut])
def list_users(
    _: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> list[UserOut]:
    return [UserOut.of(u) for u in db.scalars(select(User).order_by(User.created_at, User.email))]


@router.post("/users", response_model=UserWithToken, status_code=201)
def create_invited_user(
    body: CreateUser,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserWithToken:
    ctx = ctx_of(request)
    email = normalize_email(body.email)
    if not valid_email(email):
        raise http_error(422, "INVALID_EMAIL")
    name = body.name.strip()
    if not name or any(ord(c) < 32 for c in name):
        raise http_error(422, "INVALID_NAME")
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
    )
    db.commit()
    return UserWithToken(user=UserOut.of(user), token=_token_out(token, row))


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
        user.status = (
            "invited" if body.status == "active" and not user.password_hash else body.status
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


@router.post("/users/{user_id}/invitation", response_model=TokenOut)
def reissue_invitation(
    user_id: uuid.UUID,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> TokenOut:
    user = _get_user(db, user_id)
    if user.status != "invited":
        raise http_error(409, "NOT_INVITED")
    token, row = issue_token(
        db, user, "invitation", ctx_of(request).settings.invitation_ttl_seconds, admin.user.id
    )
    audit(
        db, "user.invitation_reissued", actor=admin.user.id, target=user.id, ip=client_ip(request)
    )
    db.commit()
    return _token_out(token, row)


@router.post("/users/{user_id}/password-reset", response_model=TokenOut)
def issue_password_reset(
    user_id: uuid.UUID,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> TokenOut:
    """Issues a one-time reset token; the old password is never visible. The user's sessions
    stay valid until the reset is completed (or an admin revokes them)."""
    user = _get_user(db, user_id)
    if user.status != "active":
        raise http_error(409, "NOT_ACTIVE")
    token, row = issue_token(
        db, user, "reset", ctx_of(request).settings.reset_ttl_seconds, admin.user.id
    )
    audit(db, "user.reset_issued", actor=admin.user.id, target=user.id, ip=client_ip(request))
    db.commit()
    return _token_out(token, row)


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
