"""People picker and in-app notifications (M08). Both are actor-scoped: nothing here names a file
or folder the caller cannot open, and no email address is ever returned."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth import clock
from app.auth.deps import Principal, get_db, http_error
from app.auth.schemas import Ok
from app.db.models import HIDDEN_NODE_STATUSES, Node, Notification, User
from app.policy import engine
from app.policy.deps import actor_access
from app.policy.model import Actor, Privilege

router = APIRouter(prefix="/api/v1", tags=["sharing"])


class PersonOut(BaseModel):
    id: uuid.UUID
    name: str
    # Only when another active account has the same display name: the first letter of the email
    # and its domain ("a***@example.com"), enough to tell two people apart and nothing more.
    hint: str | None = None


def _mask(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    at: datetime
    read: bool
    actor_name: str | None
    # Only while the reader can still open that folder; a revoked share names nothing.
    node_id: uuid.UUID | None
    node_name: str | None
    level: str | None


class NotificationsOut(BaseModel):
    unread: int
    items: list[NotificationOut]


@router.get("/people", response_model=list[PersonOut])
def find_people(
    principal: Principal = Depends(actor_access),
    db: Session = Depends(get_db),
    q: str = Query(default="", max_length=100),
) -> list[PersonOut]:
    """Active accounts other than the caller, by display name: id and name only."""
    query = select(User.id, User.name, User.email).where(
        User.status == "active", User.id != principal.user.id
    )
    term = q.strip()
    if term:
        query = query.where(func.lower(User.name).contains(term.lower(), autoescape=True))
    rows = db.execute(query.order_by(func.lower(User.name)).limit(20)).all()
    counts: dict[str, int] = {
        str(name): int(n)
        for name, n in db.execute(
            select(func.lower(User.name), func.count())
            .where(User.status == "active")
            .group_by(func.lower(User.name))
        ).all()
    }
    return [
        PersonOut(
            id=r.id,
            name=r.name,
            hint=_mask(r.email) if counts.get(r.name.lower(), 0) > 1 else None,
        )
        for r in rows
    ]


@router.get("/notifications", response_model=NotificationsOut)
def list_notifications(
    principal: Principal = Depends(actor_access),
    db: Session = Depends(get_db),
    limit: int = Query(default=30, ge=1, le=100),
) -> NotificationsOut:
    me = principal.user.id
    actor = Actor(me)
    rows = list(
        db.scalars(
            select(Notification)
            .where(Notification.recipient_user_id == me)
            .order_by(Notification.created_at.desc(), Notification.id)
            .limit(limit)
        )
    )
    unread = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.recipient_user_id == me, Notification.read_at.is_(None))
    )
    names = {
        r.id: r.name
        for r in db.execute(
            select(User.id, User.name).where(
                User.id.in_({n.actor_user_id for n in rows if n.actor_user_id})
            )
        )
    }
    items: list[NotificationOut] = []
    for n in rows:
        node = db.get(Node, n.node_id) if n.node_id else None
        visible = (
            node is not None
            and node.status not in HIDDEN_NODE_STATUSES
            and Privilege.TRAVERSE in engine.effective(db, actor, node)
        )
        items.append(
            NotificationOut(
                id=n.id,
                kind=n.kind,
                at=n.created_at,
                read=n.read_at is not None,
                actor_name=names.get(n.actor_user_id) if n.actor_user_id else None,
                node_id=node.id if visible and node else None,
                node_name=node.name if visible and node else None,
                level=n.level,
            )
        )
    return NotificationsOut(unread=unread or 0, items=items)


@router.post("/notifications/read", response_model=Ok)
def mark_all_read(
    principal: Principal = Depends(actor_access), db: Session = Depends(get_db)
) -> Ok:
    db.execute(
        update(Notification)
        .where(Notification.recipient_user_id == principal.user.id, Notification.read_at.is_(None))
        .values(read_at=clock.utcnow())
    )
    db.commit()
    return Ok()


@router.post("/notifications/{notification_id}/read", response_model=Ok)
def mark_read(
    notification_id: uuid.UUID,
    principal: Principal = Depends(actor_access),
    db: Session = Depends(get_db),
) -> Ok:
    row = db.get(Notification, notification_id)
    if row is None or row.recipient_user_id != principal.user.id:
        raise http_error(404, "NOT_FOUND")
    if row.read_at is None:
        row.read_at = clock.utcnow()
        db.commit()
    return Ok()
