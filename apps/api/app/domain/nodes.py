"""Storage roots and node identity. Callers own the transaction (explicit commit).

M03 creates only root nodes (and folder nodes for tests/fixtures); M04 adds the public create path
and must rewrite descendant lineages in the same transaction as a move (ADR-004 §2).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import clock
from app.db.models import AppSetting, Node, NodeGrant, StorageRoot, User
from app.storage.names import validate_name

DEFAULT_ACCESS_KEY = "shared_tree_default_access"
DEFAULT_ACCESS_VALUES = ("none", "viewer", "editor")
SHARED_DIR = "shared"


def shared_default_access(db: Session) -> str:
    row = db.get(AppSetting, DEFAULT_ACCESS_KEY)
    value = row.value if row is not None else "editor"
    return value if value in DEFAULT_ACCESS_VALUES else "none"  # fail closed on a bad value


def set_shared_default_access(db: Session, value: str) -> None:
    if value not in DEFAULT_ACCESS_VALUES:
        raise ValueError(value)
    stored: Any = value
    row = db.get(AppSetting, DEFAULT_ACCESS_KEY)
    if row is None:
        db.add(AppSetting(key=DEFAULT_ACCESS_KEY, value=stored))
    else:
        row.value = stored
    db.flush()


def create_root_node(db: Session, root: StorageRoot, name: str) -> Node:
    now = clock.utcnow()
    node_id = uuid.uuid4()
    node = Node(
        id=node_id,
        root_id=root.id,
        parent_id=None,
        name=name,
        kind="dir",
        lineage=f"/{node_id}/",
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(node)
    db.flush()
    return node


def root_node(db: Session, root_id: uuid.UUID) -> Node:
    return db.scalars(select(Node).where(Node.root_id == root_id, Node.parent_id.is_(None))).one()


def ensure_shared_root(db: Session) -> Node:
    """Returns the shared tree's root node, creating the root row on a database that has none."""
    root = db.scalar(select(StorageRoot).where(StorageRoot.kind == "shared"))
    if root is None:
        root = StorageRoot(
            kind="shared",
            owner_user_id=None,
            relative_dir=SHARED_DIR,
            status="active",
            created_at=clock.utcnow(),
        )
        db.add(root)
        db.flush()
        return create_root_node(db, root, "Shared files")
    return root_node(db, root.id)


def provision_user_storage(db: Session, user: User) -> StorageRoot:
    """One personal root per account plus the instance-default grant on the shared tree. Called
    from `auth.service.create_user`, the only account-creation path."""
    root = StorageRoot(
        kind="personal",
        owner_user_id=user.id,
        relative_dir=f"personal/{user.id}",  # stable id, never the email or display name
        status="active",
        created_at=clock.utcnow(),
    )
    db.add(root)
    db.flush()
    create_root_node(db, root, "My files")
    level = shared_default_access(db)
    if level != "none":
        shared = ensure_shared_root(db)
        db.add(
            NodeGrant(
                node_id=shared.id,
                grantee_user_id=user.id,
                level=level,
                granted_by=None,
                created_at=clock.utcnow(),
            )
        )
        db.flush()
    return root


def create_child_dir(db: Session, parent: Node, name: str) -> Node:
    """Database identity only; the caller creates the directory. Used by fixtures until M04."""
    if parent.kind != "dir":
        raise ValueError("parent is not a directory")
    clean = validate_name(name)
    now = clock.utcnow()
    node_id = uuid.uuid4()
    node = Node(
        id=node_id,
        root_id=parent.root_id,
        parent_id=parent.id,
        name=clean,
        kind="dir",
        lineage=f"{parent.lineage}{node_id}/",
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(node)
    db.flush()
    return node


def path_components(db: Session, node: Node) -> list[str]:
    """Names from just below the root node down to `node`, from the lineage ids."""
    ids = [uuid.UUID(p) for p in node.lineage.strip("/").split("/")][1:]
    if not ids:
        return []
    names = {n.id: n.name for n in db.scalars(select(Node).where(Node.id.in_(ids)))}
    return [names[i] for i in ids]
