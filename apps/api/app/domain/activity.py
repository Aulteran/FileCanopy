"""Per-node activity history (M07 plan, acceptance items 1, 2 and 5).

Reading a node's history needs `read` on that node. A folder's history lists the events of
everything beneath it: `read` on a folder is `read` on its whole subtree (grants only ever add),
so nothing is shown that the caller could not already open. People appear by display name, never
by email; external changes name no one. Only whitelisted, safe detail keys are returned, and ids of
other nodes only when the caller can see that node.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import HIDDEN_NODE_STATUSES, FileEvent, Node, User
from app.domain.grants import SHARE_ACTIONS
from app.policy import engine
from app.policy.model import Actor, Privilege

PLAIN_KEYS = (
    "name",
    "old_name",
    "to_name",
    "size",
    "entries",
    "reason",
    "how",
    "kind",
    "uncertain",
    "recovered",
    "proposal_id",
    "purge_after",
    "level",
)
NODE_KEYS = ("copied_from", "from_node_id", "to_node_id", "from_parent_id", "to_parent_id")


@dataclass
class EventOut:
    id: int
    node_id: uuid.UUID
    at: datetime
    action: str
    actor_kind: str
    actor_name: str | None  # display name of the user, or of the user an AI agent acted for
    source: str
    details: dict[str, Any]


def _names(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return {
        row.id: row.name for row in db.execute(select(User.id, User.name).where(User.id.in_(ids)))
    }


def _can_see_node(db: Session, actor: Actor, raw: object, cache: dict[str, bool]) -> bool:
    key = str(raw)
    if key not in cache:
        try:
            node = db.get(Node, uuid.UUID(key))
        except ValueError:
            node = None
        cache[key] = (
            node is not None
            and node.status not in HIDDEN_NODE_STATUSES
            and Privilege.TRAVERSE in engine.effective(db, actor, node)
        )
    return cache[key]


def _row_visible(db: Session, actor: Actor, node_id: uuid.UUID, cache: dict[str, bool]) -> bool:
    """An event is shown only while its own node is visible to the actor, trashed or not, so a
    folder's history never reveals what happened inside a locked Confidential folder."""
    key = f"row:{node_id}"
    if key not in cache:
        node = db.get(Node, node_id)
        cache[key] = (
            node is not None
            and Privilege.TRAVERSE in engine.effective(db, actor, node, include_hidden=True)
        )
    return cache[key]


def _may_see_sharing(db: Session, actor: Actor, node_id: uuid.UUID, cache: dict[str, bool]) -> bool:
    key = f"share:{node_id}"
    if key not in cache:
        node = db.get(Node, node_id)
        cache[key] = node is not None and engine.can_administer_grants(db, actor, node)
    return cache[key]


def node_activity(
    db: Session,
    actor_id: uuid.UUID,
    node: Node,
    *,
    limit: int,
    before: int | None,
    session_id: uuid.UUID | None = None,
) -> tuple[list[EventOut], int | None]:
    """Newest first. `before` is the id of the last event of the previous page."""
    query = select(FileEvent)
    if node.kind == "dir":
        query = query.join(Node, Node.id == FileEvent.node_id).where(
            Node.lineage.startswith(node.lineage, autoescape=True)
        )
    else:
        query = query.where(FileEvent.node_id == node.id)
    if before is not None:
        query = query.where(FileEvent.id < before)
    rows = list(db.scalars(query.order_by(FileEvent.id.desc()).limit(limit + 1)))
    next_before = rows[limit - 1].id if len(rows) > limit else None
    rows = rows[:limit]
    grantee_ids = {
        uuid.UUID(r.details["grantee_id"])
        for r in rows
        if r.action in SHARE_ACTIONS and isinstance(r.details, dict) and "grantee_id" in r.details
    }
    people = _names(
        db,
        {uid for r in rows for uid in (r.actor_user_id, r.on_behalf_of_user_id) if uid is not None}
        | grantee_ids,
    )
    actor = Actor(actor_id, session_id=session_id)
    seen: dict[str, bool] = {}
    out: list[EventOut] = []
    for r in rows:
        if not _row_visible(db, actor, r.node_id, seen):
            continue  # e.g. inside a locked Confidential folder: its history is not shown either
        if r.action in SHARE_ACTIONS and not _may_see_sharing(db, actor, r.node_id, seen):
            continue  # who else has access is for whoever manages the sharing, not for readers
        raw: dict[str, Any] = r.details if isinstance(r.details, dict) else {}
        details = {k: raw[k] for k in PLAIN_KEYS if k in raw}
        if r.action in SHARE_ACTIONS and "grantee_id" in raw:  # names are read, never stored
            details["grantee_name"] = people.get(uuid.UUID(raw["grantee_id"]), "Someone")
        for k in NODE_KEYS:
            if k in raw and _can_see_node(db, actor, raw[k], seen):
                details[k] = raw[k]
        who = r.actor_user_id or r.on_behalf_of_user_id
        out.append(
            EventOut(
                id=r.id,
                node_id=r.node_id,
                at=r.at,
                action=r.action,
                actor_kind=r.actor_kind,
                actor_name=people.get(who) if who is not None else None,
                source=r.source,
                details=details,
            )
        )
    return out, next_before
