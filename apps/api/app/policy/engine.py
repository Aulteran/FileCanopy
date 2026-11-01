"""The single content-authorization decision function (ADR-004).

Everything that touches a node (endpoints, previews, search, workers, MCP) must go through
`effective()` / `require()`. Nothing here is cached: each call reads the database, so a revoked
grant or disabled account is effective on the very next request of every existing session.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import HIDDEN_NODE_STATUSES, Node, NodeGrant, StorageRoot, User
from app.policy.model import ALL_PRIVILEGES, LEVELS, Actor, Privilege


class Forbidden(Exception):
    """Uniform denial: identical for unauthorized and nonexistent nodes (no existence oracle)."""


@dataclass(frozen=True)
class Decision:
    privileges: frozenset[Privilege]

    def allows(self, privilege: Privilege) -> bool:
        return privilege in self.privileges


DENY = Decision(frozenset())
# Roots nothing may be done under: disabled by an admin, or not on the library device (ADR-011 §3).
UNUSABLE_ROOT_STATUSES = frozenset({"disabled", "unsupported_mount"})


def lineage_ids(node: Node) -> list[uuid.UUID]:
    return [uuid.UUID(part) for part in node.lineage.strip("/").split("/")]


def decide(
    db: Session, actor: Actor, node: Node | None, *, include_hidden: bool = False
) -> Decision:
    """Hidden (dismissed, trashed, purged) nodes behave as nonexistent. Only the trash code asks
    about them (`include_hidden`), to decide who may see or restore a trashed item (ADR-017 §3)."""
    if node is None or (node.status in HIDDEN_NODE_STATUSES and not include_hidden):
        return DENY
    user = db.get(User, actor.user_id)
    if user is None or user.status != "active":  # disabled/unknown accounts are always denied
        return DENY
    root = db.get(StorageRoot, node.root_id)
    if root is None or root.status in UNUSABLE_ROOT_STATUSES:
        return DENY
    if root.kind == "personal" and root.owner_user_id == actor.user_id:
        return Decision(ALL_PRIVILEGES)
    levels = db.scalars(
        select(NodeGrant.level).where(
            NodeGrant.grantee_user_id == actor.user_id, NodeGrant.node_id.in_(lineage_ids(node))
        )
    ).all()
    granted: frozenset[Privilege] = frozenset()
    for level in levels:
        granted |= LEVELS.get(level, frozenset())
    return Decision(granted)


def effective(
    db: Session, actor: Actor, node: Node | None, *, include_hidden: bool = False
) -> frozenset[Privilege]:
    return decide(db, actor, node, include_hidden=include_hidden).privileges


def require(db: Session, actor: Actor, node_id: uuid.UUID, privilege: Privilege) -> Node:
    """Loads the node and returns it only if `actor` holds `privilege`; else raises Forbidden.
    A missing node and a denied node take the same path."""
    node = db.get(Node, node_id)
    if not decide(db, actor, node).allows(privilege) or node is None:
        raise Forbidden
    return node


def can_administer_grants(db: Session, actor: Actor, node: Node) -> bool:
    """Personal tree: the owner (`share`). Shared tree: an active administrator. Administering a
    shared-tree grant is not holding access: reading still needs a grant on the admin's account."""
    root = db.get(StorageRoot, node.root_id)
    if root is None or root.status in UNUSABLE_ROOT_STATUSES:  # nothing under it is managed
        return False
    if root.kind == "personal":
        return Privilege.SHARE in effective(db, actor, node)
    user = db.get(User, actor.user_id)
    return user is not None and user.status == "active" and user.role == "admin"


def entry_points(db: Session, actor: Actor) -> list[Node]:
    """Top-most folders the actor can start from: own personal root plus grant nodes that are not
    nested under another grant. Never returns an ancestor of a grant."""
    user = db.get(User, actor.user_id)
    if user is None or user.status != "active":
        return []
    own_root = db.scalar(
        select(Node)
        .join(StorageRoot, StorageRoot.id == Node.root_id)
        .where(
            StorageRoot.owner_user_id == actor.user_id,
            Node.parent_id.is_(None),
            StorageRoot.status.not_in(UNUSABLE_ROOT_STATUSES),
        )
    )
    granted = list(
        db.scalars(
            select(Node)
            .join(NodeGrant, NodeGrant.node_id == Node.id)
            .join(StorageRoot, StorageRoot.id == Node.root_id)
            .where(
                NodeGrant.grantee_user_id == actor.user_id,
                StorageRoot.status.not_in(UNUSABLE_ROOT_STATUSES),
                Node.status.not_in(HIDDEN_NODE_STATUSES),
            )
        )
    )
    granted_ids = {n.id for n in granted}
    top = [n for n in granted if not (set(lineage_ids(n)[:-1]) & granted_ids)]
    result = [own_root] if own_root is not None else []
    result.extend(n for n in top if own_root is None or n.id != own_root.id)
    return result


def filter_visible(db: Session, actor: Actor, node_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """The ids the actor may see (`traverse`), in order. Anything that refers to nodes indirectly
    (collections, search hits, notifications, saved links) must pass its references through here,
    so a reference never reveals more than the node's own policy would."""
    return [i for i in node_ids if Privilege.TRAVERSE in effective(db, actor, db.get(Node, i))]
