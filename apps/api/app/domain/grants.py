"""Folder grants. Every change is audited with ids only (no names, no paths)."""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import clock
from app.auth.service import audit
from app.db.models import Node, NodeGrant


def set_grant(
    db: Session, node: Node, grantee_id: uuid.UUID, level: str, by: uuid.UUID, ip: str | None
) -> NodeGrant:
    if level not in ("viewer", "editor") or node.kind != "dir":
        raise ValueError("invalid grant")
    grant: NodeGrant | None = None
    for _ in range(2):  # a concurrent creator may win the unique constraint once
        grant = db.scalar(
            select(NodeGrant).where(
                NodeGrant.node_id == node.id, NodeGrant.grantee_user_id == grantee_id
            )
        )
        if grant is not None:
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
    return removed
