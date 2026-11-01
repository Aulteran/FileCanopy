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

NOTICE_CAP = 200
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
    # Keep the newest NOTICE_CAP per person: notices are a short feed, not an archive.
    old = db.scalars(
        select(Notification.id)
        .where(Notification.recipient_user_id == recipient)
        .order_by(Notification.created_at.desc(), Notification.id)
        .offset(NOTICE_CAP - 1)
    ).all()
    if old:
        db.execute(delete(Notification).where(Notification.id.in_(old)))
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
        events.record(
            db,
            node.id,
            "share_changed" if previous else "shared",
            source="ui",
            actor_user_id=by,
            grantee_id=str(grantee_id),
            level=level,
        )
        kind = "share_changed" if previous else "shared"
        notify(db, grantee_id, kind, node_id=node.id, actor=by, level=level)
    db.flush()
    return grant


def revoke_grant(
    db: Session, node: Node, grantee_id: uuid.UUID, by: uuid.UUID, ip: str | None
) -> bool:
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
        )
        if not left:
            notify(db, grantee_id, "unshared", node_id=node.id, actor=by)
    return removed


def chain_ids(node: Node) -> list[uuid.UUID]:
    return [uuid.UUID(p) for p in node.lineage.strip("/").split("/")]


def inherited_grants(db: Session, node: Node) -> list[tuple[NodeGrant, Node]]:
    """Grants on the folders above `node` (not on it): who can reach `node` through a bigger
    share. Only for whoever manages the sharing of `node`."""
    above = chain_ids(node)[:-1]
    if not above:
        return []
    rows = db.execute(
        select(NodeGrant, Node)
        .join(Node, Node.id == NodeGrant.node_id)
        .where(NodeGrant.node_id.in_(above))
        .order_by(NodeGrant.created_at)
    ).all()
    return [(g, n) for g, n in rows]


def users_with_access(db: Session, node_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not node_ids:
        return set()
    return set(
        db.scalars(
            select(NodeGrant.grantee_user_id)
            .join(User, User.id == NodeGrant.grantee_user_id)
            .where(NodeGrant.node_id.in_(node_ids), User.status == "active")
        )
    )


def descendant_grantees(db: Session, node: Node) -> set[uuid.UUID]:
    return set(
        db.scalars(
            select(NodeGrant.grantee_user_id)
            .join(Node, Node.id == NodeGrant.node_id)
            .join(User, User.id == NodeGrant.grantee_user_id)
            .where(User.status == "active", Node.lineage.startswith(node.lineage, autoescape=True))
        )
    )


def move_impact(
    db: Session, node: Node, target: Node, *, copy: bool
) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """(gain, lose): people who start to be able to open the item, and who stop. A same-area move
    keeps the grants on the item itself; a move to another area leaves every grant on the original
    (which goes to the trash); a copy only ever adds people."""
    before = users_with_access(db, chain_ids(node))
    after = users_with_access(db, chain_ids(target))
    if copy:
        return after, set()
    if node.root_id == target.root_id:
        after |= users_with_access(db, [node.id])
        return after - before, before - after
    before |= descendant_grantees(db, node)
    return after - before, before - after
