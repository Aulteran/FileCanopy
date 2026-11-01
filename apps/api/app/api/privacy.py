"""Privacy labels, Confidential unlock and lock, and the instance switch (M09).

The unlock verifies the account password on the server with the same throttle as login. Nothing
here trusts a value from the browser about whether a folder is unlocked.
"""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
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
)
from app.auth.pin_router import check_pin_for_session
from app.auth.schemas import Ok
from app.auth.service import audit
from app.db.models import AppSetting, AuthAudit, ConfidentialUnlock, Node
from app.domain import events
from app.policy import privacy
from app.policy.deps import (
    NodeContext,
    admin_access,
    grant_admin_access,
    unlock_access,
)
from app.policy.model import Actor

router = APIRouter(prefix="/api/v1", tags=["privacy"])

SETTING_KEY = "privacy.labels_enabled"
MAX_UNLOCK_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=10)


def labels_enabled(db: Session) -> bool:
    row = db.get(AppSetting, SETTING_KEY)
    return True if row is None else bool(row.value)


class PrivacyOut(BaseModel):
    own: str
    effective: str
    locked: bool
    # Seconds of idle time left before the lock closes; null unless unlocked.
    locks_in: int | None = None
    labels_enabled: bool


class UnlockBody(BaseModel):
    """The six-digit PIN once the person has one; the account password only until then (M12b)."""

    model_config = ConfigDict(extra="forbid")
    pin: str | None = Field(default=None, max_length=32)
    password: str | None = Field(default=None, min_length=1, max_length=1024)


class LabelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: Literal["normal", "private", "confidential"]
    # Required to lower Private (a tick) ...
    confirm_lowering: bool = False
    # ... and to lower Confidential (the PIN, or the password until a PIN is set; checked here).
    pin: str | None = Field(default=None, max_length=32)
    password: str | None = Field(default=None, max_length=1024)


class AdminPrivacyOut(BaseModel):
    labels_enabled: bool
    # Counts only: an administrator never learns names of anyone's protected folders.
    private_folders: int
    confidential_folders: int


class AdminPrivacyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    labels_enabled: bool
    # Switching off keeps every existing label enforced; the administrator must say they know.
    confirm_labels_stay: bool = False


def describe(db: Session, actor: Actor, node: Node) -> PrivacyOut:
    eff = privacy.effective_label(db, node)
    scope = privacy.confidential_scope(db, node)
    locks_in: int | None = None
    locked = False
    if scope is not None:
        row = privacy.live_unlock(db, actor.user_id, actor.session_id, scope)
        if row is None:
            locked = True
        else:
            left = row.last_activity_at + privacy.IDLE_LOCK - clock.utcnow()
            locks_in = max(0, int(left.total_seconds()))
    return PrivacyOut(
        own=node.privacy_label,
        effective=eff,
        locked=locked,
        locks_in=locks_in,
        labels_enabled=labels_enabled(db),
    )


def _verify_password(request: Request, db: Session, principal: Principal, password: str) -> None:
    ctx, ip, user = ctx_of(request), client_ip(request), principal.user
    keys = ctx.throttle.login_keys(user.email, ip)
    if (retry := ctx.throttle.retry_after(db, keys)) is not None:
        audit(db, "privacy.throttled", actor=user.id, ip=ip)
        db.commit()
        raise http_error(429, "TOO_MANY_ATTEMPTS", headers={"Retry-After": str(retry)})
    if not ctx.passwords.verify(user.password_hash, password):
        ctx.throttle.record_failure(db, keys)
        audit(db, "privacy.unlock_failed", actor=user.id, target=user.id, ip=ip)
        db.flush()
        recent = db.scalar(
            select(func.count())
            .select_from(AuthAudit)
            .where(
                AuthAudit.action == "privacy.unlock_failed",
                AuthAudit.actor_user_id == user.id,
                AuthAudit.at >= clock.utcnow() - FAILURE_WINDOW,
            )
        )
        if (recent or 0) >= MAX_UNLOCK_FAILURES:
            # Someone is guessing the password from a logged-in session: end it. The message
            # says so, so the owner does not mistake it for a bug.
            principal.session.revoked_at = clock.utcnow()
            db.execute(
                delete(ConfidentialUnlock).where(
                    ConfidentialUnlock.session_id == principal.session.id
                )
            )
            audit(db, "privacy.session_ended", actor=user.id, target=user.id, ip=ip)
            db.commit()
            raise http_error(401, "SESSION_ENDED_TOO_MANY_UNLOCK_FAILURES")
        db.commit()
        raise http_error(403, "INVALID_CREDENTIALS")
    ctx.throttle.clear(db, keys[:1])


def _verify_secret(
    request: Request, db: Session, principal: Principal, pin: str | None, password: str | None
) -> None:
    """The Confidential secret. A person with a PIN must use it (the password is refused, so a
    stolen password does not open Confidential from a stolen session); a person without one may
    use the password until they set a PIN. An account with no password needs a PIN first."""
    user = principal.user
    if ctx_of(request).pins.is_set(db, user.id):
        if pin is None:
            raise http_error(403, "PIN_REQUIRED")
        check_pin_for_session(request, db, principal, pin)
        return
    if pin is not None:
        raise http_error(409, "PIN_NOT_SET")
    if user.password_hash is None:
        raise http_error(403, "PIN_REQUIRED")
    if not password:
        raise http_error(403, "PASSWORD_REQUIRED")
    _verify_password(request, db, principal, password)


@router.get("/nodes/{node_id}/privacy", response_model=PrivacyOut)
def get_privacy(
    ctx: NodeContext = Depends(unlock_access), db: Session = Depends(get_db)
) -> PrivacyOut:
    return describe(db, Actor(ctx.principal.user.id, session_id=ctx.principal.session.id), ctx.node)


@router.post("/nodes/{node_id}/unlock", response_model=PrivacyOut)
def unlock(
    body: UnlockBody,
    request: Request,
    ctx: NodeContext = Depends(unlock_access),
    db: Session = Depends(get_db),
) -> PrivacyOut:
    """Unlocks the outermost Confidential folder at or above this node for this login session."""
    principal, node = ctx.principal, ctx.node
    actor = Actor(principal.user.id, session_id=principal.session.id)
    scope = privacy.confidential_scope(db, node)
    if scope is None:
        raise http_error(409, "NOT_CONFIDENTIAL")
    _verify_secret(request, db, principal, body.pin, body.password)
    now = clock.utcnow()
    row = db.scalar(
        select(ConfidentialUnlock).where(
            ConfidentialUnlock.session_id == principal.session.id,
            ConfidentialUnlock.scope_node_id == scope,
        )
    )
    if row is None:
        db.add(
            ConfidentialUnlock(
                session_id=principal.session.id,
                user_id=principal.user.id,
                scope_node_id=scope,
                created_at=now,
                last_activity_at=now,
            )
        )
    else:
        row.user_id, row.created_at, row.last_activity_at = principal.user.id, now, now
    audit(db, "privacy.unlock", actor=principal.user.id, ip=client_ip(request), node_id=str(scope))
    db.commit()
    return describe(db, actor, node)


@router.post("/nodes/{node_id}/lock", response_model=PrivacyOut)
def lock(
    request: Request,
    ctx: NodeContext = Depends(unlock_access),
    db: Session = Depends(get_db),
) -> PrivacyOut:
    principal, node = ctx.principal, ctx.node
    scope = privacy.confidential_scope(db, node)
    if scope is not None:
        db.execute(
            delete(ConfidentialUnlock).where(
                ConfidentialUnlock.session_id == principal.session.id,
                ConfidentialUnlock.scope_node_id == scope,
            )
        )
        audit(
            db, "privacy.lock", actor=principal.user.id, ip=client_ip(request), node_id=str(scope)
        )
        db.commit()
    return describe(db, Actor(principal.user.id, session_id=principal.session.id), node)


@router.post("/auth/lock", response_model=Ok)
def lock_all(
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> Ok:
    """Locks every Confidential folder for this login session at once."""
    db.execute(
        delete(ConfidentialUnlock).where(ConfidentialUnlock.session_id == principal.session.id)
    )
    audit(db, "privacy.lock_all", actor=principal.user.id, ip=client_ip(request))
    db.commit()
    return Ok()


@router.put("/nodes/{node_id}/privacy", response_model=PrivacyOut)
def set_label(
    body: LabelBody,
    request: Request,
    ctx: NodeContext = Depends(grant_admin_access),
    db: Session = Depends(get_db),
) -> PrivacyOut:
    """Sets this folder's own label. Raising is free for whoever manages the folder; lowering
    needs a tick (Private) or the password (Confidential); switching the feature off blocks only
    new labels. The guard also keeps a locked folder shut: it grants no `share` until unlocked."""
    principal, node = ctx.principal, ctx.node
    if node.kind != "dir" or node.status != "active":
        raise http_error(422, "NOT_A_DIRECTORY")
    current, wanted = node.privacy_label, body.label
    if privacy.RANK[wanted] < privacy.RANK[current] and privacy.locked_scopes_in(
        db, principal.user.id, principal.session.id, node
    ):
        # Also in the shared tree, where an administrator needs no content access to get here:
        # lowering needs the folder to be unlocked (and so a grant), not just the role.
        raise http_error(403, "LOCKED")
    if privacy.RANK[wanted] > privacy.RANK[current] and not labels_enabled(db):
        raise http_error(409, "PRIVACY_LABELS_DISABLED")
    if privacy.RANK[wanted] < privacy.RANK[current]:
        if current == "confidential":
            _verify_secret(request, db, principal, body.pin, body.password)
        elif not body.confirm_lowering:
            raise http_error(409, "CONFIRMATION_REQUIRED")
    if wanted != current:
        node.privacy_label = wanted
        if wanted != "confidential":  # nothing left to unlock at this node
            db.execute(
                delete(ConfidentialUnlock).where(ConfidentialUnlock.scope_node_id == node.id)
            )
        audit(
            db,
            "privacy.label_changed",
            actor=principal.user.id,
            ip=client_ip(request),
            node_id=str(node.id),
            old=current,
            new=wanted,
        )
        events.record(
            db,
            node.id,
            "label_changed",
            source="ui",
            actor_user_id=principal.user.id,
            old=current,
            level=wanted,
        )
        db.commit()
    return describe(db, Actor(principal.user.id, session_id=principal.session.id), node)


@router.get("/admin/privacy", response_model=AdminPrivacyOut)
def admin_get(
    principal: Principal = Depends(admin_access), db: Session = Depends(get_db)
) -> AdminPrivacyOut:
    return _admin_out(db)


def _admin_out(db: Session) -> AdminPrivacyOut:
    counts = dict(
        db.execute(
            select(Node.privacy_label, func.count())
            .where(Node.privacy_label != "normal", Node.status == "active")
            .group_by(Node.privacy_label)
        ).all()
    )
    return AdminPrivacyOut(
        labels_enabled=labels_enabled(db),
        private_folders=int(counts.get("private", 0)),
        confidential_folders=int(counts.get("confidential", 0)),
    )


@router.put("/admin/privacy", response_model=AdminPrivacyOut)
def admin_set(
    body: AdminPrivacyBody,
    request: Request,
    principal: Principal = Depends(admin_access),
    db: Session = Depends(get_db),
) -> AdminPrivacyOut:
    """Never relabels anything. Off means: no new Private or Confidential labels; the existing
    ones keep protecting their folders until their owners lower them on purpose."""
    held = _admin_out(db)
    if (
        not body.labels_enabled
        and (held.private_folders or held.confidential_folders)
        and not body.confirm_labels_stay
    ):
        raise http_error(409, "CONFIRMATION_REQUIRED")
    row = db.get(AppSetting, SETTING_KEY)
    if row is None:
        db.add(AppSetting(key=SETTING_KEY, value=body.labels_enabled))
    else:
        row.value = body.labels_enabled
    audit(
        db,
        "privacy.labels_enabled" if body.labels_enabled else "privacy.labels_disabled",
        actor=principal.user.id,
        ip=client_ip(request),
    )
    db.commit()
    return _admin_out(db)
