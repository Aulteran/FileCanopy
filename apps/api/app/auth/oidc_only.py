"""OIDC-only mode (M12b): the administrator switches password sign-in off for the instance.

The mode is only ever in force while *all three* hold: the switch is on, OIDC is configured, and
the operator has not set FILECANOPY_FORCE_LOCAL_LOGIN. Taking the provider configuration away
therefore brings password sign-in back by itself, and the environment variable and the command
line (`python -m app.auth.cli oidc-only off`) are the recovery paths for a broken provider.
"""

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, client_ip, ctx_of, get_db, http_error, require_admin
from app.auth.pin_router import consume_reauth
from app.auth.schemas import Strict
from app.auth.service import audit
from app.config import Settings
from app.db.models import AppSetting, User, UserIdentity

SETTING_KEY = "auth.oidc_only"
REAUTH_FOR_SWITCH = timedelta(minutes=10)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def setting_on(db: Session) -> bool:
    row = db.get(AppSetting, SETTING_KEY)
    return bool(row.value) if row is not None else False


def effective(db: Session, settings: Settings) -> bool:
    return setting_on(db) and settings.oidc_enabled and not settings.force_local_login


def require_password_login(db: Session, settings: Settings) -> None:
    if effective(db, settings):
        raise http_error(403, "PASSWORD_LOGIN_DISABLED")


def _linked_users(settings: Settings) -> Select[Any]:
    return select(UserIdentity.user_id).where(UserIdentity.issuer == settings.oidc_issuer)


def linked_admins(db: Session, settings: Settings, *, excluding: User | None = None) -> int:
    stmt = (
        select(func.count())
        .select_from(User)
        .where(
            User.role == "admin",
            User.status == "active",
            User.id.in_(_linked_users(settings)),
        )
    )
    if excluding is not None:
        stmt = stmt.where(User.id != excluding.id)
    return int(db.scalar(stmt) or 0)


def unlinked_users(db: Session, settings: Settings) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.status == "active", User.id.not_in(_linked_users(settings)))
        )
        or 0
    )


def is_unlinked(db: Session, settings: Settings, user: User) -> bool:
    return (
        db.scalar(
            select(UserIdentity.id).where(
                UserIdentity.user_id == user.id, UserIdentity.issuer == settings.oidc_issuer
            )
        )
        is None
    )


def guard_linked_admin(db: Session, settings: Settings, target: User) -> None:
    """While the mode is in force, never remove the last administrator who can still sign in."""
    if not effective(db, settings):
        return
    if target.role == "admin" and target.status == "active":
        if linked_admins(db, settings, excluding=target) == 0:
            raise http_error(409, "LAST_LINKED_ADMIN")


class OidcOnlyOut(BaseModel):
    enabled: bool  # the switch
    effective: bool  # in force right now
    oidc_configured: bool
    force_local_login: bool
    linked_admins: int
    unlinked_users: int
    admin_linked: bool  # the asking administrator can turn it on


class OidcOnlyBody(Strict):
    enabled: bool
    # People who cannot sign in afterwards (active, no provider link): the administrator must
    # repeat the number they were shown.
    confirm_unlinked_users: int | None = None


def _out(db: Session, settings: Settings, admin: User) -> OidcOnlyOut:
    return OidcOnlyOut(
        enabled=setting_on(db),
        effective=effective(db, settings),
        oidc_configured=settings.oidc_enabled,
        force_local_login=settings.force_local_login,
        linked_admins=linked_admins(db, settings) if settings.oidc_enabled else 0,
        unlinked_users=unlinked_users(db, settings) if settings.oidc_enabled else 0,
        admin_linked=settings.oidc_enabled
        and db.scalar(
            select(UserIdentity.id).where(
                UserIdentity.user_id == admin.id, UserIdentity.issuer == settings.oidc_issuer
            )
        )
        is not None,
    )


@router.get("/oidc-only", response_model=OidcOnlyOut)
def get_mode(
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> OidcOnlyOut:
    return _out(db, ctx_of(request).settings, admin.user)


@router.put("/oidc-only", response_model=OidcOnlyOut)
def put_mode(
    body: OidcOnlyBody,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> OidcOnlyOut:
    s, ip = ctx_of(request).settings, client_ip(request)
    row = db.get(AppSetting, SETTING_KEY)
    if body.enabled:
        # Cheap refusals first; the single-use re-authentication is spent last.
        if not s.oidc_enabled:
            raise http_error(409, "OIDC_NOT_CONFIGURED")
        if s.force_local_login:
            raise http_error(409, "FORCE_LOCAL_LOGIN_SET")
        if not _out(db, s, admin.user).admin_linked:
            raise http_error(409, "ADMIN_NOT_LINKED")
        unlinked = unlinked_users(db, s)
        if unlinked and body.confirm_unlinked_users != unlinked:
            raise HTTPException(
                status_code=409, detail={"code": "UNLINKED_USERS", "count": unlinked}
            )
        if not consume_reauth(db, admin, ttl=REAUTH_FOR_SWITCH):
            db.commit()
            audit(db, "auth.oidc_only_blocked", actor=admin.user.id, ip=ip, reason="no_reauth")
            db.commit()
            raise http_error(403, "REAUTH_REQUIRED")
    if row is None:
        db.add(AppSetting(key=SETTING_KEY, value=body.enabled))
    else:
        row.value = body.enabled
    audit(
        db,
        "auth.oidc_only_on" if body.enabled else "auth.oidc_only_off",
        actor=admin.user.id,
        ip=ip,
        unlinked=unlinked_users(db, s) if body.enabled else None,
    )
    db.commit()
    return _out(db, s, admin.user)
