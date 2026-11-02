"""Virtual collections (M18): groups of file references, nothing more.

A collection stores node ids. It never moves, copies or renames a file, and it grants nothing:
every member is decided per person, at request time, by the same policy engine as everything
else. A member the person cannot read now (no grant, trashed, locked Confidential, disabled
account) is left out *before* anything is counted, named or returned, so neither a name nor a
number can reveal a file the person cannot see. Sharing a collection shares only its title and the
members the other person could already read.

This module imports no file-operation, storage, trash, undo or grant code (the tests check the
imports), so it has no way to change a file or a permission.
"""

import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import CursorResult, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import clock
from app.auth.service import audit
from app.db.models import Collection, CollectionMember, CollectionShare, Node, User
from app.policy import engine
from app.policy.model import Actor, Privilege

MAX_COLLECTIONS = 200  # owned per person
MAX_MEMBERS = 2000  # per collection
MAX_SHARES = 50
MAX_ADD = 200  # files per request
MAX_TITLE = 80
LEVELS = ("viewer", "editor")


class CollectionError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


NOT_FOUND = CollectionError(404, "NOT_FOUND")  # missing and forbidden look alike


def clean_title(raw: str | None) -> str | None:
    if not raw:
        return None
    text = unicodedata.normalize("NFC", raw)
    # Format characters (bidi overrides, zero-width marks) can make a title read as another one.
    text = "".join(
        " " if unicodedata.category(c) in ("Cc", "Zl", "Zp") else c
        for c in text
        if unicodedata.category(c) != "Cf"
    )
    text = " ".join(text.split())
    if not text or len(text) > MAX_TITLE or not any(c.isalnum() for c in text):
        return None
    return text


def title_key(title: str) -> str:
    return title.casefold()


@dataclass
class Access:
    collection: Collection
    role: str  # owner | editor | viewer


def access(db: Session, user_id: uuid.UUID, collection_id: uuid.UUID) -> Access:
    """The person's role on a collection, or the uniform not-found."""
    user = db.get(User, user_id)
    coll = db.get(Collection, collection_id)
    if user is None or user.status != "active" or coll is None:
        raise NOT_FOUND
    if coll.owner_user_id == user_id:
        return Access(coll, "owner")
    share = db.get(CollectionShare, (collection_id, user_id))
    if share is None:
        raise NOT_FOUND
    return Access(coll, share.level)


def can_read(db: Session, actor: Actor, node: Node | None) -> bool:
    return (
        node is not None
        and node.kind == "file"
        and Privilege.READ in engine.effective(db, actor, node)
    )


def readable_members(db: Session, actor: Actor, collection_id: uuid.UUID) -> list[Node]:
    """The members this person may read now, by name. The only way members are listed."""
    nodes = db.scalars(
        select(Node)
        .join(CollectionMember, CollectionMember.node_id == Node.id)
        .where(CollectionMember.collection_id == collection_id)
        .order_by(Node.name_key, Node.id)
        .limit(MAX_MEMBERS)
    ).all()
    return [n for n in nodes if can_read(db, actor, n)]


def parent_of(db: Session, actor: Actor, node: Node) -> Node | None:
    """The folder a member lives in, only when the person may see that folder."""
    parent = db.get(Node, node.parent_id) if node.parent_id else None
    if parent is not None and Privilege.TRAVERSE in engine.effective(db, actor, parent):
        return parent
    return None


@dataclass
class Summary:
    collection: Collection
    role: str
    count: int  # readable members only
    owner_name: str | None  # for a collection shared with the person


def list_for(db: Session, actor: Actor) -> list[Summary]:
    user = db.get(User, actor.user_id)
    if user is None or user.status != "active":
        return []
    rows = db.execute(
        select(Collection, CollectionShare.level)
        .outerjoin(
            CollectionShare,
            (CollectionShare.collection_id == Collection.id)
            & (CollectionShare.user_id == actor.user_id),
        )
        .where(or_(Collection.owner_user_id == actor.user_id, CollectionShare.user_id.is_not(None)))
        .order_by(func.lower(Collection.title), Collection.id)
        .limit(MAX_COLLECTIONS * 2)
    ).all()
    out: list[Summary] = []
    for coll, level in rows:
        owner = coll.owner_user_id == actor.user_id
        owner_row = None if owner else db.get(User, coll.owner_user_id)
        out.append(
            Summary(
                coll,
                "owner" if owner else str(level),
                len(readable_members(db, actor, coll.id)),
                owner_row.name if owner_row is not None else None,
            )
        )
    return out


def create(
    db: Session,
    owner_id: uuid.UUID,
    title: str,
    ip: str | None,
    *,
    node_ids: list[uuid.UUID] | None = None,
    suggestion_id: uuid.UUID | None = None,
    actor: Actor | None = None,
) -> Collection:
    """The only place a collection is created. Called by the create route and by the approval of
    an AI suggestion (never by anything that merely suggests). Does not commit."""
    clean = clean_title(title)
    if clean is None:
        raise CollectionError(422, "BAD_TITLE")
    owned = db.scalar(
        select(func.count()).select_from(Collection).where(Collection.owner_user_id == owner_id)
    )
    if (owned or 0) >= MAX_COLLECTIONS:
        raise CollectionError(409, "TOO_MANY_COLLECTIONS")
    if db.scalar(
        select(Collection.id).where(
            Collection.owner_user_id == owner_id, Collection.title_key == title_key(clean)
        )
    ):
        raise CollectionError(409, "TITLE_TAKEN")
    now = clock.utcnow()
    coll = Collection(
        id=uuid.uuid4(),
        owner_user_id=owner_id,
        title=clean,
        title_key=title_key(clean),
        created_at=now,
        updated_at=now,
        suggestion_id=suggestion_id,
    )
    db.add(coll)
    db.flush()
    if node_ids:
        add_members(db, actor or Actor(owner_id), coll, node_ids, ip, quiet=True)
    audit(
        db,
        "collection.created",
        actor=owner_id,
        ip=ip,
        collection_id=str(coll.id),
        members=len(node_ids or []),
        suggestion_id=str(suggestion_id) if suggestion_id else None,
    )
    return coll


def rename(db: Session, acc: Access, title: str, ip: str | None) -> None:
    if acc.role != "owner":
        raise CollectionError(403, "FORBIDDEN")
    clean = clean_title(title)
    if clean is None:
        raise CollectionError(422, "BAD_TITLE")
    taken = db.scalar(
        select(Collection.id).where(
            Collection.owner_user_id == acc.collection.owner_user_id,
            Collection.title_key == title_key(clean),
            Collection.id != acc.collection.id,
        )
    )
    if taken:
        raise CollectionError(409, "TITLE_TAKEN")
    acc.collection.title = clean
    acc.collection.title_key = title_key(clean)
    acc.collection.updated_at = clock.utcnow()
    audit(
        db,
        "collection.renamed",
        actor=acc.collection.owner_user_id,
        ip=ip,
        collection_id=str(acc.collection.id),
    )


def delete_collection(db: Session, acc: Access, ip: str | None) -> None:
    """Deleting a collection deletes only the references; no file is touched."""
    if acc.role != "owner":
        raise CollectionError(403, "FORBIDDEN")
    cid = acc.collection.id
    db.execute(delete(CollectionMember).where(CollectionMember.collection_id == cid))
    db.execute(delete(CollectionShare).where(CollectionShare.collection_id == cid))
    db.delete(acc.collection)
    audit(
        db, "collection.deleted", actor=acc.collection.owner_user_id, ip=ip, collection_id=str(cid)
    )


def add_members(
    db: Session,
    actor: Actor,
    coll: Collection,
    node_ids: list[uuid.UUID],
    ip: str | None,
    *,
    quiet: bool = False,
) -> int:
    """Adds references to files the actor can read. A file the actor cannot read is refused the
    way a missing one is. Adding an existing member is a no-op. Does not commit."""
    ids = list(dict.fromkeys(node_ids))[:MAX_ADD]
    nodes: list[Node] = []
    for node_id in ids:
        node = db.get(Node, node_id)
        if node is None or not can_read(db, actor, node):
            raise NOT_FOUND
        nodes.append(node)
    have = set(
        db.scalars(
            select(CollectionMember.node_id).where(CollectionMember.collection_id == coll.id)
        )
    )
    total = db.scalar(
        select(func.count())
        .select_from(CollectionMember)
        .where(CollectionMember.collection_id == coll.id)
    )
    fresh = [n for n in nodes if n.id not in have]
    if (total or 0) + len(fresh) > MAX_MEMBERS:
        raise CollectionError(409, "COLLECTION_FULL")
    now = clock.utcnow()
    for node in fresh:
        db.add(
            CollectionMember(
                collection_id=coll.id, node_id=node.id, added_by=actor.user_id, added_at=now
            )
        )
    if fresh:
        coll.updated_at = now
        if not quiet:
            audit(
                db,
                "collection.members_added",
                actor=actor.user_id,
                ip=ip,
                collection_id=str(coll.id),
                count=len(fresh),
            )
    try:
        db.flush()
    except IntegrityError:  # added at the same moment by someone else: same end state
        db.rollback()
        raise CollectionError(409, "CONFLICT") from None
    return len(fresh)


def remove_members(
    db: Session, actor: Actor, coll: Collection, node_ids: list[uuid.UUID], ip: str | None
) -> int:
    """Removes references the actor can read (an unreadable member is refused like a missing
    one, so removal cannot be used to probe for it). Does not commit."""
    ids = list(dict.fromkeys(node_ids))[:MAX_ADD]
    for node_id in ids:
        if not can_read(db, actor, db.get(Node, node_id)):
            raise NOT_FOUND
    removed: CursorResult[Any] = db.execute(  # type: ignore[assignment]
        delete(CollectionMember).where(
            CollectionMember.collection_id == coll.id, CollectionMember.node_id.in_(ids)
        )
    )
    if removed.rowcount:
        coll.updated_at = clock.utcnow()
        audit(
            db,
            "collection.members_removed",
            actor=actor.user_id,
            ip=ip,
            collection_id=str(coll.id),
            count=int(removed.rowcount),
        )
    return int(removed.rowcount)


@dataclass
class ShareRow:
    user_id: uuid.UUID
    name: str
    level: str


def shares_of(db: Session, acc: Access) -> list[ShareRow]:
    if acc.role != "owner":
        return []
    rows = db.execute(
        select(CollectionShare.user_id, User.name, CollectionShare.level)
        .join(User, User.id == CollectionShare.user_id)
        .where(CollectionShare.collection_id == acc.collection.id)
        .order_by(func.lower(User.name))
    ).all()
    return [ShareRow(r[0], r[1], r[2]) for r in rows]


def set_share(db: Session, acc: Access, user_id: uuid.UUID, level: str, ip: str | None) -> None:
    if acc.role != "owner":
        raise CollectionError(403, "FORBIDDEN")
    target = db.get(User, user_id)
    if level not in LEVELS or target is None or target.status != "active":
        raise CollectionError(422, "BAD_SHARE")
    if user_id == acc.collection.owner_user_id:
        raise CollectionError(422, "BAD_SHARE")
    row = db.get(CollectionShare, (acc.collection.id, user_id))
    if row is None:
        count = db.scalar(
            select(func.count())
            .select_from(CollectionShare)
            .where(CollectionShare.collection_id == acc.collection.id)
        )
        if (count or 0) >= MAX_SHARES:
            raise CollectionError(409, "TOO_MANY_SHARES")
        db.add(
            CollectionShare(
                collection_id=acc.collection.id,
                user_id=user_id,
                level=level,
                created_at=clock.utcnow(),
            )
        )
    else:
        row.level = level
    audit(
        db,
        "collection.shared",
        actor=acc.collection.owner_user_id,
        target=user_id,
        ip=ip,
        collection_id=str(acc.collection.id),
        level=level,
    )


def remove_share(
    db: Session, acc: Access, user_id: uuid.UUID, by: uuid.UUID, ip: str | None
) -> None:
    """The owner removes a person, or a person leaves a collection shared with them (`by`)."""
    if acc.role != "owner" and user_id != by:
        raise CollectionError(403, "FORBIDDEN")
    db.execute(
        delete(CollectionShare).where(
            CollectionShare.collection_id == acc.collection.id, CollectionShare.user_id == user_id
        )
    )
    audit(
        db,
        "collection.unshared",
        actor=by,
        target=user_id,
        ip=ip,
        collection_id=str(acc.collection.id),
    )


def collections_of(db: Session, actor: Actor, node: Node) -> list[Collection]:
    """Collections (visible to the person) that reference a file the person can read."""
    if not can_read(db, actor, node):
        return []
    colls = db.scalars(
        select(Collection)
        .join(CollectionMember, CollectionMember.collection_id == Collection.id)
        .outerjoin(
            CollectionShare,
            (CollectionShare.collection_id == Collection.id)
            & (CollectionShare.user_id == actor.user_id),
        )
        .where(
            CollectionMember.node_id == node.id,
            or_(Collection.owner_user_id == actor.user_id, CollectionShare.user_id.is_not(None)),
        )
        .order_by(func.lower(Collection.title), Collection.id)
    ).all()
    return list(colls)
