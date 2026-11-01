"""Folder grants. Every change is audited with ids only in the security audit, written to the
folder's own activity history (`file_event`) and announced to the person it concerns by an in-app
notice (M08). Nothing here carries content, paths or email addresses."""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import clock
from app.auth.service import audit
from app.db.models import Node, NodeGrant, Notification, User
from app.domain import events

SHARE_ACTIONS = frozenset({"shared", "share_changed", "unshared", "share_left"})


def notify(
    db: Session,
    recipient: uuid.UUID,
    kind: str,
    *,
    node_id: uuid.UUID,
    actor: uuid.UUID | None,
    level: str | None = None,
) -> None:
    db.add(
        Notification(
            recipient_user_id=recipient,
            kind=kind,
            node_id=node_id,
            actor_user_id=actor,
            level=level,
            created_at=clock.utcnow(),
        )
    )


def set_grant(
    db: Session, node: Node, grantee_id: uuid.UUID, level: str, by: uuid.UUID, ip: str | None
) -> NodeGrant:
    if level not in ("viewer", "editor") or node.kind != "dir":
        raise ValueError("invalid grant")
    grant: NodeGrant | None = None
    previous: str | None = None
    for _ in range(2):  # a concurrent creator may win the unique constraint once
        grant = db.scalar(
            select(NodeGrant).where(
                NodeGrant.node_id == node.id, NodeGrant.grantee_user_id == grantee_id
            )
        )
        if grant is not None:
            previous = grant.level
            grant.level = level
            break
        candidate = NodeGrant(
            node_id=node.id,
            grantee_user_id=grantee_id,
            level=level,
            granted_by=by,
            created_at=clock.utcnow(),
        )
        try:
            with db.begin_nested():
                db.add(candidate)
            grant = candidate
            break
        except IntegrityError:
            continue
    if grant is None:
        raise RuntimeError("grant could not be stored")
    audit(db, "grant.set", actor=by, target=grantee_id, ip=ip, node_id=str(node.id), level=level)
    if previous != level:  # saving an unchanged choice is not news
        grantee = db.get(User, grantee_id)
        events.record(
            db,
            node.id,
            "share_changed" if previous else "shared",
            source="ui",
            actor_user_id=by,
            grantee_id=str(grantee_id),
            grantee_name=grantee.name if grantee else None,
            level=level,
        )
        notify(db, grantee_id, "shared", node_id=node.id, actor=by, level=level)
    db.flush()
    return grant


def revoke_grant(
    db: Session, node: Node, grantee_id: uuid.UUID, by: uuid.UUID, ip: str | None
) -> bool:
    grantee = db.get(User, grantee_id)
    result = db.execute(
        delete(NodeGrant).where(
            NodeGrant.node_id == node.id, NodeGrant.grantee_user_id == grantee_id
        )
    )
    removed = bool(result.rowcount)  # type: ignore[attr-defined]
    if removed:
        audit(db, "grant.revoked", actor=by, target=grantee_id, ip=ip, node_id=str(node.id))
        left = by == grantee_id
        events.record(
            db,
            node.id,
            "share_left" if left else "unshared",
            source="ui",
            actor_user_id=by,
            grantee_id=str(grantee_id),
            grantee_name=grantee.name if grantee else None,
        )
        if not left:
            notify(db, grantee_id, "unshared", node_id=node.id, actor=by)
    return removed
