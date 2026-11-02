"""Trash, restore, permanent delete, retention and cross-root moves (ADR-017, ADR-013 §7).

Trashed entries are renamed into `<library>/.filecanopy-trash/<root_id>/<item_id>/<name>`: a sibling
of the root directories on the library device, so the rename is instant and needs no extra space,
and it is outside every root, so users, the scanner and backups never see it. A trash failure never
falls back to a hard delete. Paths are derived from ids and never stored or returned.

Every mutation follows the action contract of `04_DATA_FILESYSTEM.md` (authorize, journal, claim,
filesystem step, one database transaction) and is covered by startup recovery (`recover_one`): a
rename that completed is rolled forward, one that provably did not start is rolled back, and
anything else says `reconcile_needed`. A cross-root move that is interrupted leaves both copies,
never a lost file.
"""

import hashlib
import logging
import os
import stat
import threading
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, literal, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.auth import clock
from app.auth.service import audit
from app.config import Settings
from app.db.models import (
    HIDDEN_NODE_STATUSES,
    AppSetting,
    FileOperation,
    Node,
    StorageRoot,
    TrashItem,
)
from app.domain import events
from app.domain.fileops import (
    OpCtx,
    OpError,
    Outcome,
    _begin,
    _candidates,
    _chain_idle,
    _clean,
    _copy_for_move,
    _db_has,
    _fs,
    _insert_copied,
    _map_error,
    _need,
    _note,
    _peek_replay,
    _record_failure,
    _release_claim,
    _replayed,
    _root,
    _set,
    _stat_at,
    _undo_marker,
    _usable,
    serialize_tree_changes,
)
from app.domain.fileops import (
    _unlocked_subtree as fileops_unlocked,
)
from app.domain.nodes import path_components
from app.policy import engine, privacy
from app.policy.model import Actor, Privilege
from app.preview.cache import discard_trash_item
from app.storage import files
from app.storage.names import name_key
from app.storage.resolver import (
    DIR_FLAGS,
    StorageError,
    StorageMissing,
    open_dir,
)

log = logging.getLogger("filecanopy.trash")

TRASH_DIR = ".filecanopy-trash"
RETENTION_KEY = "trash_retention_days"
DEFAULT_RETENTION_DAYS = 30
MIN_RETENTION_DAYS = 7
MAX_RETENTION_DAYS = 3650
WARNING_DAYS = 7
REASONS = ("deleted", "moved_out", "import_undone", "copy_undone", "split_undone")
LIVE_ITEM_STATUSES = ("in_trash", "busy", "reconcile_needed")


# ------------------------------------------------------------------ retention setting


def get_retention(db: Session) -> int | None:
    """Days an item stays in the trash (0 = never purge automatically). None when the stored value
    cannot be trusted: nothing is purged then (ADR-017 §4)."""
    row = db.get(AppSetting, RETENTION_KEY)
    if row is None:
        return DEFAULT_RETENTION_DAYS
    value = row.value
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value == 0 or MIN_RETENTION_DAYS <= value <= MAX_RETENTION_DAYS:
        return value
    return None


def valid_retention(days: int) -> bool:
    return days == 0 or MIN_RETENTION_DAYS <= days <= MAX_RETENTION_DAYS


def set_retention(db: Session, days: int, *, by: uuid.UUID, ip: str | None) -> None:
    """Stores the setting. Lowering it never shortens items already in the trash; raising it (or
    turning purging on after `0`) extends or sets their deadline, so a change can never purge
    something sooner than the date the person was shown."""
    if not valid_retention(days):
        raise ValueError("retention out of range")
    was_off = get_retention(db) in (0, None)  # deadlines may have passed while nothing purged
    row = db.get(AppSetting, RETENTION_KEY)
    if row is None:
        db.add(AppSetting(key=RETENTION_KEY, value=days))
    else:
        row.value = days
    now = clock.utcnow()
    if days > 0:
        fresh = now + timedelta(days=days)
        for item in db.scalars(select(TrashItem).where(TrashItem.status.in_(LIVE_ITEM_STATUSES))):
            wanted = item.trashed_at + timedelta(days=days)
            if was_off:
                wanted = max(wanted, fresh)  # turning purging on never deletes at once
            if item.purge_after is None or wanted > item.purge_after:
                item.purge_after = wanted
    audit(db, "trash.retention_set", actor=by, ip=ip, days=days)
    db.flush()


def _deadline(db: Session, trashed_at: datetime) -> datetime | None:
    days = get_retention(db)
    days = DEFAULT_RETENTION_DAYS if days is None else days
    return trashed_at + timedelta(days=days) if days > 0 else None


# ------------------------------------------------------------------ trash directory


def _unavailable() -> OpError:
    return OpError(503, "TRASH_UNAVAILABLE")


def ensure_trash_base(library: Path) -> None:
    """Creates `<library>/.filecanopy-trash` (0700) and checks it is a real directory on the
    library device. Raises OpError(503, TRASH_UNAVAILABLE) otherwise: a symlink, a file or another
    filesystem disables trash and deletes are refused (never turned into a hard delete)."""
    try:
        lib_fd = os.open(library, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as exc:
        raise _unavailable() from exc
    try:
        try:
            os.mkdir(TRASH_DIR, 0o700, dir_fd=lib_fd)
        except FileExistsError:
            pass
        except OSError as exc:
            raise _unavailable() from exc
        try:
            fd = os.open(TRASH_DIR, DIR_FLAGS, dir_fd=lib_fd)  # O_NOFOLLOW | O_DIRECTORY
        except OSError as exc:
            raise _unavailable() from exc
        try:
            if os.fstat(fd).st_dev != os.fstat(lib_fd).st_dev:
                raise _unavailable()
            os.fchmod(fd, 0o700)
        except OSError as exc:
            raise _unavailable() from exc
        finally:
            os.close(fd)
    finally:
        os.close(lib_fd)


@contextmanager
def _trash_open(
    ctx: OpCtx, root_id: uuid.UUID, item_id: uuid.UUID | None, *, create: bool
) -> Iterator[int]:
    """The directory of one item (`item_id`) or of a whole root's trash (`item_id=None`). Only the
    opening is translated to coded errors; the body's own errors pass through."""
    library = ctx.settings.library_path
    if create:
        ensure_trash_base(library)
    parts = f"{TRASH_DIR}/{root_id}" + (f"/{item_id}" if item_id is not None else "")
    manager = open_dir(library, parts, create=create)
    try:
        fd = manager.__enter__()
    except StorageMissing as exc:
        raise OpError(409, "CONTENT_MISSING") from exc
    except (StorageError, OSError) as exc:
        raise _unavailable() from exc
    try:
        if os.fstat(fd).st_dev != os.stat(library).st_dev:
            raise _unavailable()
        yield fd
    finally:
        manager.__exit__(None, None, None)


def _discard_item_dir(ctx: OpCtx, root_id: uuid.UUID, item_id: uuid.UUID) -> None:
    """Removes an item's directory once it is empty (best effort; a leftover is harmless)."""
    try:
        with _trash_open(ctx, root_id, None, create=False) as fd:
            os.rmdir(str(item_id), dir_fd=fd)
    except (OpError, OSError):
        pass


# ------------------------------------------------------------------ visibility


def item_visible(
    db: Session, actor_id: uuid.UUID, item: TrashItem, session_id: uuid.UUID | None = None
) -> bool:
    """ADR-017 §3. The owner of a personal root sees all its items. Anyone else sees the items of
    the shared tree whose original parent they can still write, and in a personal root only their
    own, while they still hold `write` there. An administrator is not special."""
    root = db.get(StorageRoot, item.root_id)
    if root is None:
        return False
    parent = db.get(Node, item.original_parent_id)
    privileges = engine.effective(
        db, Actor(actor_id, session_id=session_id), parent, include_hidden=True
    )
    if not privileges:
        return False
    if root.kind == "personal" and root.owner_user_id == actor_id:
        return True
    if Privilege.WRITE not in privileges:
        return False
    return root.kind != "personal" or item.trashed_by == actor_id


def can_manage_root(
    db: Session, actor_id: uuid.UUID, root_node: Node, session_id: uuid.UUID | None = None
) -> bool:
    """Whole-root actions: the owner of a personal folder, an administrator for the shared tree."""
    return root_node.parent_id is None and engine.can_administer_grants(
        db, Actor(actor_id, session_id=session_id), root_node
    )


def confirm_token(item: TrashItem) -> str:
    """What a person confirms before deleting forever: it changes if the item's size or count do."""
    raw = f"purge:{item.id}:{item.entry_count}:{item.size_bytes}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def empty_token(root_id: uuid.UUID, items: list[TrashItem]) -> str:
    raw = f"empty:{root_id}:" + ",".join(sorted(f"{i.id}:{i.size_bytes}" for i in items))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def visible_items(
    db: Session,
    actor_id: uuid.UUID,
    *,
    root_id: uuid.UUID | None = None,
    limit: int = 200,
    after: tuple[datetime, uuid.UUID] | None = None,
    session_id: uuid.UUID | None = None,
) -> tuple[list[TrashItem], bool]:
    """Newest first. Returns up to `limit` visible items and whether more follow."""
    out: list[TrashItem] = []
    cursor = after
    while True:
        query = select(TrashItem).where(TrashItem.status.in_(LIVE_ITEM_STATUSES))
        if root_id is not None:
            query = query.where(TrashItem.root_id == root_id)
        if cursor is not None:
            at, last = cursor
            query = query.where(
                (TrashItem.trashed_at < at) | ((TrashItem.trashed_at == at) & (TrashItem.id < last))
            )
        rows = list(
            db.scalars(query.order_by(TrashItem.trashed_at.desc(), TrashItem.id.desc()).limit(500))
        )
        for item in rows:
            if item_visible(db, actor_id, item, session_id):
                if len(out) == limit:
                    return out, True
                out.append(item)
        if len(rows) < 500:
            return out, False
        cursor = (rows[-1].trashed_at, rows[-1].id)


# ------------------------------------------------------------------ subtree bookkeeping


def _subtree_stats(db: Session, node: Node) -> tuple[int, int]:
    count, size = db.execute(
        select(func.count(), func.coalesce(func.sum(Node.size), 0)).where(
            Node.lineage.startswith(node.lineage, autoescape=True),
            Node.status.not_in(HIDDEN_NODE_STATUSES),
        )
    ).one()
    return int(count or 0), int(size or 0)


def _busy_below(db: Session, node: Node) -> bool:
    busy = db.scalar(
        select(func.count())
        .select_from(Node)
        .where(
            Node.lineage.startswith(node.lineage, autoescape=True),
            Node.id != node.id,
            Node.status == "busy",
        )
    )
    return bool(busy)


def _mark_trashed(
    ctx: OpCtx,
    node: Node,
    *,
    item_id: uuid.UUID,
    reason: str,
    trashed_at: datetime,
    purge_after: datetime | None,
    entries: int,
    size: int,
    op: FileOperation,
    event: str,
    event_details: dict[str, Any],
    trashed_by: uuid.UUID | None = None,
) -> TrashItem:
    """Database half of trashing, in the caller's transaction: the subtree (except entries that
    are already in the trash under their own item) gets the item's id and the status `trashed`."""
    db = ctx.db
    parent_id = node.parent_id
    if parent_id is None:
        raise OpError(422, "CANNOT_TRASH_ROOT")
    db.execute(
        update(Node)
        .where(
            Node.lineage.startswith(node.lineage, autoescape=True),
            Node.status.not_in(HIDDEN_NODE_STATUSES),
        )
        .values(status="trashed", trash_item_id=item_id, updated_at=trashed_at)
        .execution_options(synchronize_session=False)
    )
    discard_trash_item(db, ctx.settings, item_id)  # previews go with the file (M10)
    db.execute(
        update(Node)
        .where(Node.id == node.id)
        .values(revision=Node.revision + 1)
        .execution_options(synchronize_session=False)
    )
    item = TrashItem(
        id=item_id,
        root_id=node.root_id,
        node_id=node.id,
        original_parent_id=parent_id,
        original_name=node.name,
        kind=node.kind,
        size_bytes=size,
        entry_count=entries,
        trashed_by=trashed_by or ctx.actor,
        trashed_at=trashed_at,
        reason=reason,
        purge_after=purge_after,
        status="in_trash",
    )
    db.add(item)
    events.record(
        db,
        node.id,
        event,
        correlation_id=op.id,
        **ctx.event_actor(),
        name=node.name,
        reason=reason,
        trash_item_id=str(item_id),
        entries=entries,
        size=size,
        purge_after=purge_after.isoformat() if purge_after else None,
        **event_details,
    )
    db.flush()
    db.expire_all()
    return item


# ------------------------------------------------------------------ trash


@dataclass
class TrashOutcome:
    item: TrashItem
    node: Node
    op: FileOperation | None
    replay: bool = False


def _item_of(op: FileOperation, ctx: OpCtx) -> TrashItem | None:
    raw = (op.detail or {}).get("trash_id")
    return ctx.db.get(TrashItem, uuid.UUID(raw)) if raw else None


def _replay_trash(ctx: OpCtx, op: FileOperation) -> TrashOutcome:
    item = _item_of(op, ctx)
    node = ctx.db.get(Node, op.node_id) if op.node_id else None
    if item is None or node is None or not item_visible(ctx.db, ctx.actor, item, ctx.session_id):
        raise OpError(403, "FORBIDDEN")
    return TrashOutcome(item, node, op, replay=True)


def trash(
    ctx: OpCtx,
    node_id: uuid.UUID,
    *,
    expected_revision: int,
    key: str | None,
    reason: str = "deleted",
    undo_of: uuid.UUID | None = None,
    event: str = "trashed",
    event_details: dict[str, Any] | None = None,
) -> TrashOutcome:
    """Moves a file or folder tree to the trash. Needs `write` on the parent (so the root of a
    grant cannot be trashed by its grantee, as with rename)."""
    if reason not in REASONS:
        raise ValueError(reason)
    params = {"node": str(node_id), "rev": expected_revision, "reason": reason}
    done_before = _peek_replay(ctx, "trash", key, params)
    if done_before is not None:
        return _replay_trash(ctx, done_before)
    existing = ctx.db.get(Node, node_id)
    if (
        existing is not None
        and existing.status in HIDDEN_NODE_STATUSES
        and Privilege.WRITE
        in engine.effective(
            ctx.db,
            Actor(ctx.actor, ctx.actor_kind, session_id=ctx.session_id),
            existing,
            include_hidden=True,
        )
    ):
        raise OpError(409, "ALREADY_TRASHED")
    node = _need(ctx, node_id, Privilege.WRITE)
    if node.parent_id is None:
        raise OpError(422, "CANNOT_TRASH_ROOT")
    parent = _need(ctx, node.parent_id, Privilege.WRITE)
    _usable(parent, kind="dir")
    _usable(node)
    if node.revision != expected_revision:
        raise OpError(409, "REVISION_CONFLICT")
    if _busy_below(ctx.db, node):
        raise OpError(409, "NODE_BUSY")
    root = _root(ctx, node)
    ensure_trash_base(ctx.settings.library_path)
    entries, size = _subtree_stats(ctx.db, node)
    trash_id = uuid.uuid4()
    now = clock.utcnow()
    purge_after = _deadline(ctx.db, now)
    old_name = node.name
    op, replay = _begin(
        ctx,
        "trash",
        key,
        node_id=node.id,
        target_parent_id=None,
        params=params,
        detail={
            "src_parent": str(parent.id),
            "src_name": old_name,
            "trash_id": str(trash_id),
            "reason": reason,
            "entries": entries,
            "size": size,
            "purge_after": purge_after.isoformat() if purge_after else None,
            **({"undo_of": str(undo_of)} if undo_of is not None else {}),
        },
    )
    if replay:
        return _replay_trash(ctx, op)
    names = path_components(ctx.db, node)
    claimed = ctx.db.execute(
        update(Node)
        .where(Node.id == node.id, Node.revision == expected_revision, Node.status == "active")
        .values(status="busy")
    )
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        err = OpError(409, "REVISION_CONFLICT")
        _record_failure(ctx, op, err)
        raise err
    ctx.db.commit()

    done = False
    try:
        with ExitStack() as stack:
            src_fd = stack.enter_context(_fs(ctx, root, names[:-1], write=True))
            info = files.lstat_entry(src_fd, old_name)
            if info is None:
                _set(op, "reconcile_needed", error="CONTENT_MISSING")
                ctx.db.execute(
                    update(Node).where(Node.id == node.id).values(status="reconcile_needed")
                )
                ctx.db.commit()
                raise OpError(409, "CONTENT_MISSING")
            _set(op, "planned", src_ino=info.st_ino, src_dev=info.st_dev)
            ctx.db.commit()
            trash_fd = stack.enter_context(_trash_open(ctx, root.id, trash_id, create=True))
            _note(ctx, op, step="renaming")
            try:
                files.rename_noreplace(src_fd, old_name, trash_fd, old_name)
            except files.CrossDevice as exc:
                raise _unavailable() from exc  # never a copy-and-delete fallback
            except files.NameExists as exc:
                raise OpError(409, "NAME_CONFLICT") from exc
            _set(op, "planned", step="filesystem_done")
            try:
                serialize_tree_changes(ctx.db)
                ctx.db.expire_all()
                fresh = ctx.db.get(Node, node.id)
                if fresh is None or fresh.status != "busy":
                    raise OpError(409, "PARENT_CHANGED")
                item = _mark_trashed(
                    ctx,
                    fresh,
                    item_id=trash_id,
                    reason=reason,
                    trashed_at=now,
                    purge_after=purge_after,
                    entries=entries,
                    size=size,
                    op=op,
                    event=event,
                    event_details=event_details or {},
                )
                undo = (
                    {}
                    if undo_of is not None or reason != "deleted"
                    else {
                        "undo": {
                            "kind": "trash",
                            "item": str(trash_id),
                            "revision": expected_revision + 1,
                        }
                    }
                )
                _set(op, "committed", **undo)
                audit(
                    ctx.db,
                    "file.trash",
                    actor=ctx.actor,
                    ip=ctx.ip,
                    node_id=str(node.id),
                    item_id=str(trash_id),
                    reason=reason,
                )
                ctx.db.commit()
            except BaseException:
                ctx.db.rollback()
                _undo_trash_rename(ctx, op, node.id, src_fd, old_name, trash_fd)
                raise
            done = True
    except BaseException as exc:
        err = _map_error(exc)
        if not done:
            ctx.db.rollback()
            current = ctx.db.get(FileOperation, op.id)
            if current is not None and current.status == "planned":
                _set(current, "failed", error=err.code)
            ctx.db.commit()
            _release_claim(ctx, node.id)
            _discard_item_dir(ctx, root.id, trash_id)
        if err.status == 500:
            log.exception("trash failed unexpectedly")
        raise err from exc
    refreshed = ctx.db.get(Node, node.id)
    if refreshed is None:
        raise OpError(500, "OPERATION_FAILED")
    return TrashOutcome(item, refreshed, op)


def _undo_trash_rename(
    ctx: OpCtx, op: FileOperation, node_id: uuid.UUID, src_fd: int, name: str, trash_fd: int
) -> None:
    """The database step failed after the rename into the trash: put the entry back, or say so."""
    ok = False
    try:
        files.rename_noreplace(trash_fd, name, src_fd, name)
        ok = True
    except Exception:
        ok = False
    ctx.db.rollback()
    current = ctx.db.get(FileOperation, op.id)
    if current is not None:
        _set(
            current,
            "rolled_back" if ok else "reconcile_needed",
            step="database_failed" if ok else "database_failed_filesystem_changed",
        )
    if not ok:
        ctx.db.execute(update(Node).where(Node.id == node_id).values(status="reconcile_needed"))
    ctx.db.commit()


# ------------------------------------------------------------------ restore


def _claim_item(ctx: OpCtx, item_id: uuid.UUID, allowed: tuple[str, ...]) -> str:
    """Compare-and-swap an item to `busy`; returns the status it had."""
    item = ctx.db.get(TrashItem, item_id)
    if item is None:
        raise OpError(403, "FORBIDDEN")
    prior = item.status
    swapped = ctx.db.execute(
        update(TrashItem)
        .where(TrashItem.id == item_id, TrashItem.status.in_(allowed))
        .values(status="busy")
    )
    if swapped.rowcount != 1:  # type: ignore[attr-defined]
        ctx.db.rollback()
        raise OpError(409, "TRASH_ITEM_UNAVAILABLE")
    ctx.db.commit()
    return prior


def _release_item(ctx: OpCtx, item_id: uuid.UUID, status: str = "in_trash") -> None:
    ctx.db.rollback()
    ctx.db.execute(
        update(TrashItem)
        .where(TrashItem.id == item_id, TrashItem.status == "busy")
        .values(status=status)
    )
    ctx.db.commit()


def restore(
    ctx: OpCtx,
    item_id: uuid.UUID,
    *,
    target_parent_id: uuid.UUID | None,
    on_conflict: str,
    key: str | None,
    undo_of: uuid.UUID | None = None,
) -> Outcome:
    """Puts an item back: into its original folder by default, or into a chosen folder of the same
    root. Needs `write` on the destination. A name that is taken is never overwritten: `keep_both`
    adds a number, `fail` refuses."""
    if on_conflict not in ("keep_both", "fail"):
        raise OpError(422, "INVALID_CONFLICT_MODE")
    params = {"item": str(item_id), "parent": str(target_parent_id), "mode": on_conflict}
    done_before = _peek_replay(ctx, "restore", key, params)
    if done_before is not None:
        return _replayed(ctx, done_before)
    item = ctx.db.get(TrashItem, item_id)
    if item is None or not item_visible(ctx.db, ctx.actor, item, ctx.session_id):
        audit(ctx.db, "access.denied", actor=ctx.actor, ip=ctx.ip, item_id=str(item_id))
        ctx.db.commit()
        raise OpError(403, "FORBIDDEN")
    if item.status == "reconcile_needed":
        raise OpError(409, "RECONCILE_NEEDED")
    if item.status != "in_trash":
        raise OpError(409, "TRASH_ITEM_UNAVAILABLE")
    node = ctx.db.get(Node, item.node_id)
    if node is None or node.status != "trashed" or node.trash_item_id != item.id:
        raise OpError(409, "TRASH_ITEM_UNAVAILABLE")
    fileops_unlocked(ctx, node)
    dest_id = target_parent_id or item.original_parent_id
    dest = ctx.db.get(Node, dest_id)
    if dest is None or dest.status in HIDDEN_NODE_STATUSES:
        if target_parent_id is None:
            raise OpError(409, "PARENT_UNAVAILABLE")  # the original folder is gone: pick another
        raise OpError(403, "FORBIDDEN")
    dest = _need(ctx, dest, Privilege.WRITE)
    if dest.root_id != item.root_id:
        raise OpError(422, "RESTORE_TARGET_OTHER_ROOT")
    _usable(dest, kind="dir")
    root = _root(ctx, dest)
    mode = "keep_both" if on_conflict == "keep_both" else "reject"
    prior_status = _claim_item(ctx, item.id, ("in_trash",))
    del prior_status
    try:
        op, replay = _begin(
            ctx,
            "restore",
            key,
            node_id=node.id,
            target_parent_id=dest.id,
            params=params,
            detail={
                "trash_id": str(item.id),
                "dst_parent": str(dest.id),
                "dst_name": item.original_name,
                **({"undo_of": str(undo_of)} if undo_of is not None else {}),
            },
        )
    except BaseException:
        _release_item(ctx, item.id)
        raise
    if replay:
        _release_item(ctx, item.id, item.status)
        return _replayed(ctx, op)

    dst_comps = path_components(ctx.db, dest)
    name, kind, root_id = item.original_name, item.kind, item.root_id
    done = False
    chosen: str | None = None
    try:
        with ExitStack() as stack:
            trash_fd = stack.enter_context(_trash_open(ctx, root_id, item.id, create=False))
            info = files.lstat_entry(trash_fd, name)
            if info is None:
                _set(op, "reconcile_needed", error="CONTENT_MISSING")
                ctx.db.execute(
                    update(TrashItem)
                    .where(TrashItem.id == item.id)
                    .values(status="reconcile_needed")
                )
                ctx.db.commit()
                raise OpError(409, "CONTENT_MISSING")
            dst_fd = stack.enter_context(_fs(ctx, root, dst_comps, write=True))
            _set(op, "planned", src_ino=info.st_ino, src_dev=info.st_dev)
            ctx.db.commit()
            for cand in _candidates(name, kind, mode):
                if _db_has(ctx.db, dest.id, cand):
                    continue
                _note(ctx, op, dst_name=cand, step="renaming")
                try:
                    files.rename_noreplace(trash_fd, name, dst_fd, cand)
                    chosen = cand
                    break
                except files.NameExists:
                    continue
                except files.CrossDevice as exc:
                    raise _unavailable() from exc
            if chosen is None:
                raise OpError(409, "NAME_CONFLICT")
            _set(op, "planned", dst_name=chosen, step="filesystem_done")
            try:
                serialize_tree_changes(ctx.db)
                ctx.db.expire_all()
                target = ctx.db.get(Node, dest.id)
                fresh = ctx.db.get(Node, node.id)
                live = ctx.db.get(TrashItem, item.id)
                if target is None or fresh is None or live is None:
                    raise OpError(409, "PARENT_CHANGED")
                if target.status != "active" or not _chain_idle(ctx.db, target):
                    raise OpError(409, "PARENT_CHANGED")
                if fresh.status != "trashed" or fresh.trash_item_id != item.id:
                    raise OpError(409, "TRASH_ITEM_UNAVAILABLE")
                _apply_restore(ctx, fresh, target, chosen, item.id)
                live.status, live.finished_at = "restored", clock.utcnow()
                events.record(
                    ctx.db,
                    node.id,
                    "undone" if undo_of is not None else "restored",
                    correlation_id=op.id,
                    **ctx.event_actor(),
                    old_name=name,
                    name=chosen,
                    to_parent_id=str(target.id),
                    trash_item_id=str(item.id),
                    **_undo_marker(undo_of),
                )
                _set(op, "committed")
                audit(
                    ctx.db,
                    "file.restore",
                    actor=ctx.actor,
                    ip=ctx.ip,
                    node_id=str(node.id),
                    item_id=str(item.id),
                )
                ctx.db.commit()
            except BaseException:
                ctx.db.rollback()
                _undo_restore_rename(ctx, op, item.id, trash_fd, name, dst_fd, chosen)
                raise
            done = True
    except BaseException as exc:
        err = _map_error(exc)
        if not done:
            ctx.db.rollback()
            current = ctx.db.get(FileOperation, op.id)
            if current is not None and current.status == "planned":
                _set(current, "failed", error=err.code)
            ctx.db.commit()
            _release_item(ctx, item.id)
        if err.status == 500:
            log.exception("restore failed unexpectedly")
        raise err from exc
    _discard_item_dir(ctx, root_id, item.id)
    result = ctx.db.get(Node, node.id)
    if result is None:
        raise OpError(500, "OPERATION_FAILED")
    return Outcome(result, op)


def _apply_restore(ctx: OpCtx, node: Node, target: Node, new_name: str, item_id: uuid.UUID) -> None:
    """Database half of a restore: the entry (re)joins `target`, its lineage and those of everything
    below it are rewritten (an earlier-trashed nested item stays trashed but keeps a valid
    lineage), and the nodes this item held become active again."""
    db = ctx.db
    try:  # restoring somewhere weaker must not drop the protection the item had
        carry = privacy.carried_label(db, node, target)
    except ValueError as exc:
        raise OpError(409, "PRIVACY_DOWNGRADE") from exc
    old_lineage = node.lineage
    new_lineage = f"{target.lineage}{node.id}/"
    db.execute(
        update(Node)
        .where(Node.lineage.startswith(old_lineage, autoescape=True), Node.id != node.id)
        .values(lineage=literal(new_lineage) + func.substr(Node.lineage, len(old_lineage) + 1))
        .execution_options(synchronize_session=False)
    )
    db.execute(
        update(Node)
        .where(Node.trash_item_id == item_id, Node.id != node.id)
        .values(status="active", trash_item_id=None)
        .execution_options(synchronize_session=False)
    )
    db.execute(
        update(Node)
        .where(Node.id == node.id)
        .values(
            parent_id=target.id,
            name=new_name,
            name_key=name_key(new_name),
            lineage=new_lineage,
            status="active",
            trash_item_id=None,
            revision=Node.revision + 1,
            updated_at=clock.utcnow(),
            **({"privacy_label": carry} if carry else {}),
        )
        .execution_options(synchronize_session=False)
    )
    db.flush()
    db.expire_all()


def _undo_restore_rename(
    ctx: OpCtx,
    op: FileOperation,
    item_id: uuid.UUID,
    trash_fd: int,
    name: str,
    dst_fd: int,
    dst_name: str,
) -> None:
    ok = False
    try:
        files.rename_noreplace(dst_fd, dst_name, trash_fd, name)
        ok = True
    except Exception:
        ok = False
    ctx.db.rollback()
    current = ctx.db.get(FileOperation, op.id)
    if current is not None:
        _set(
            current,
            "rolled_back" if ok else "reconcile_needed",
            step="database_failed" if ok else "database_failed_filesystem_changed",
        )
    if not ok:
        ctx.db.execute(
            update(TrashItem).where(TrashItem.id == item_id).values(status="reconcile_needed")
        )
    ctx.db.commit()


# ------------------------------------------------------------------ permanent delete


def purge(
    ctx: OpCtx, item_id: uuid.UUID, *, confirm: str | None, key: str | None = None
) -> TrashItem:
    """Delete forever, for one item the caller can see. The confirmation token states what is
    about to go (count and size), so a stale screen cannot delete something that changed."""
    item = ctx.db.get(TrashItem, item_id)
    if item is None or not item_visible(ctx.db, ctx.actor, item, ctx.session_id):
        audit(ctx.db, "access.denied", actor=ctx.actor, ip=ctx.ip, item_id=str(item_id))
        ctx.db.commit()
        raise OpError(403, "FORBIDDEN")
    if confirm != confirm_token(item):
        raise OpError(409, "CONFIRMATION_REQUIRED")
    return purge_item(ctx, item, system=False, reason="manual", key=key)


def purge_item(
    ctx: OpCtx, item: TrashItem, *, system: bool, reason: str, key: str | None = None
) -> TrashItem:
    """Removes an item's bytes and marks its nodes `purged` (rows stay, so history stays
    readable). Authorization and confirmation are the caller's job. An item that is not simply
    `in_trash` is purged only by a person (`system=False`)."""
    allowed = ("in_trash",) if system else ("in_trash", "reconcile_needed")
    if (
        not system
    ):  # retention expiry is the system's; a person destroying Confidential data unlocks
        held = ctx.db.get(Node, item.node_id)
        if held is not None:
            fileops_unlocked(ctx, held)
    prior = _claim_item(ctx, item.id, allowed)
    entries, size, root_id, node_id, item_id = (
        item.entry_count,
        item.size_bytes,
        item.root_id,
        item.node_id,
        item.id,
    )
    try:
        op, replay = _begin(
            ctx,
            "purge",
            key,
            node_id=node_id,
            target_parent_id=None,
            params={"item": str(item_id)},
            detail={
                "trash_id": str(item_id),
                "step": "claimed",
                "system": system,
                "reason": reason,
                "prior": prior,
            },
        )
    except BaseException:
        _release_item(ctx, item_id, prior)
        raise
    if replay:
        _release_item(ctx, item_id, prior)
        return item
    started = False
    try:
        with _trash_open(ctx, root_id, None, create=False) as root_fd:
            _note(ctx, op, step="deleting")
            started = True
            try:
                files.remove_tree(root_fd, str(item_id))
            except FileNotFoundError:
                pass  # already gone: the database step below completes the purge
        _finish_purge(ctx, op, item_id, system=system, reason=reason)
    except BaseException as exc:
        err = _map_error(exc)
        ctx.db.rollback()
        current = ctx.db.get(FileOperation, op.id)
        if current is not None and current.status == "planned":
            if started:  # bytes may be gone (a disk error, or the database step after it)
                _set(current, "reconcile_needed", step="delete_failed")
                ctx.db.execute(
                    update(TrashItem)
                    .where(TrashItem.id == item_id)
                    .values(status="reconcile_needed")
                )
            else:
                _set(current, "failed", error=err.code)
                ctx.db.execute(
                    update(TrashItem)
                    .where(TrashItem.id == item_id, TrashItem.status == "busy")
                    .values(status=prior)
                )
            ctx.db.commit()
        if err.status == 500:
            log.exception("purge failed unexpectedly")
        raise err from exc
    done = ctx.db.get(TrashItem, item_id)
    if done is None:
        raise OpError(500, "OPERATION_FAILED")
    del entries, size
    return done


def _finish_purge(
    ctx: OpCtx, op: FileOperation, item_id: uuid.UUID, *, system: bool, reason: str
) -> None:
    """The database half of a purge: nodes `purged`, the item `purged`, one event, one audit row."""
    db = ctx.db
    serialize_tree_changes(db)
    db.expire_all()
    item = db.get(TrashItem, item_id)
    if item is None:
        raise OpError(500, "OPERATION_FAILED")
    now = clock.utcnow()
    db.execute(
        update(Node)
        .where(Node.trash_item_id == item_id)
        .values(status="purged", updated_at=now)
        .execution_options(synchronize_session=False)
    )
    discard_trash_item(db, ctx.settings, item_id)  # previews go with the file (M10)
    item.status, item.finished_at = "purged", now
    actor = {"actor_kind": events.SYSTEM, "source": "system"} if system else ctx.event_actor()
    events.record(
        db,
        item.node_id,
        "purged",
        correlation_id=op.id,
        **actor,
        name=item.original_name,
        trash_item_id=str(item_id),
        entries=item.entry_count,
        size=item.size_bytes,
        how=reason,
    )
    _set(op, "committed", step="complete")
    audit(
        db,
        "trash.purged",
        actor=ctx.actor,
        ip=ctx.ip,
        item_id=str(item_id),
        how=reason,
        entries=item.entry_count,
        size=item.size_bytes,
    )
    db.commit()


def empty_root_trash(ctx: OpCtx, root_node: Node, *, confirm: str | None) -> dict[str, int]:
    """Deletes forever every item in a root's trash. Needs the root's manager and the token that
    states the count and size. Items that cannot be removed are left (and flagged)."""
    if not can_manage_root(ctx.db, ctx.actor, root_node, ctx.session_id):
        audit(ctx.db, "access.denied", actor=ctx.actor, ip=ctx.ip, node_id=str(root_node.id))
        ctx.db.commit()
        raise OpError(403, "FORBIDDEN")
    items = list(
        ctx.db.scalars(
            select(TrashItem).where(
                TrashItem.root_id == root_node.root_id, TrashItem.status == "in_trash"
            )
        )
    )
    for it in items:
        held = ctx.db.get(Node, it.node_id)
        if held is not None:
            fileops_unlocked(ctx, held)
    if confirm != empty_token(root_node.root_id, items):
        raise OpError(409, "CONFIRMATION_REQUIRED")
    purged = failed = 0
    freed = 0
    for item in items:
        try:
            purge_item(ctx, item, system=False, reason="emptied")
            purged += 1
            freed += item.size_bytes
        except OpError:
            failed += 1
    return {"purged": purged, "failed": failed, "bytes": freed}


# ------------------------------------------------------------------ retention job


def purge_due(
    sessions: "sessionmaker[Session]", settings: Settings, *, limit: int = 1000
) -> dict[str, int]:
    """Purges items past their deadline. Does nothing when automatic purging is off, the setting
    cannot be read, or the trash directory is unhealthy (ADR-017 §4). Never purges early because
    the disk is full. One aggregate line (count and size, no names) goes to the log and audit."""
    counts = {"purged": 0, "failed": 0, "bytes": 0}
    with sessions() as db:
        days = get_retention(db)
        if not days:
            return counts
        try:
            ensure_trash_base(settings.library_path)
        except OpError:
            log.warning("trash purge skipped: trash directory unavailable")
            return counts
        now = clock.utcnow()
        due = list(
            db.scalars(
                select(TrashItem)
                .where(
                    TrashItem.status == "in_trash",
                    TrashItem.purge_after.is_not(None),
                    TrashItem.purge_after <= now,
                )
                .order_by(TrashItem.purge_after, TrashItem.id)
                .limit(limit)
            )
        )
        for item in due:
            ctx = OpCtx(db, settings, item.trashed_by)  # journaled under the trashing account
            try:
                purge_item(ctx, item, system=True, reason="retention")
                counts["purged"] += 1
                counts["bytes"] += item.size_bytes
            except OpError as err:
                counts["failed"] += 1
                log.warning("automatic purge failed (%s)", err.code)
                db.rollback()
        if counts["purged"] or counts["failed"]:
            audit(
                db,
                "trash.purge_run",
                purged=counts["purged"],
                failed=counts["failed"],
                bytes=counts["bytes"],
            )
            db.commit()
            log.info(
                "trash purge: %d items, %d bytes, %d failed",
                counts["purged"],
                counts["bytes"],
                counts["failed"],
            )
    return counts


class TrashService:
    """Hourly retention check in the API process (same pattern as the backup scheduler). Optional
    and never needed for CRUD."""

    def __init__(self, sessions: "sessionmaker[Session]", settings: Settings) -> None:
        self.sessions, self.settings = sessions, settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None or not self.settings.trash_purge_enabled:
            return
        self._thread = threading.Thread(target=self._loop, name="fc-trash", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        while not self._stop.wait(self.settings.trash_check_seconds):
            try:
                purge_due(self.sessions, self.settings)
            except Exception as exc:  # never takes the API down
                log.warning("trash purge run failed: %s", type(exc).__name__)


# ------------------------------------------------------------------ cross-root move


def _trash_matches_plan(
    trash_fd: int, name: str, plan: list[tuple[tuple[str, ...], str, os.stat_result]]
) -> bool:
    """After the source went to the trash, confirm nothing changed since it was copied: same
    inode, size and mtime for every entry (the top entry's ctime moved with the rename), and no
    entry added to a folder since (it would not be in the copy and would silently end up in the
    trash)."""
    expected: dict[tuple[str, ...], set[str]] = {}
    for rel, kind, _ in plan:
        if len(rel) > 1:
            expected.setdefault(rel[:-1], set()).add(rel[-1])
        if kind == "dir":
            expected.setdefault(rel, set())
    for rel, kind, info in plan:
        parts = (name, *rel[1:])
        try:
            parent_fd = os.dup(trash_fd)
            try:
                for part in parts[:-1]:
                    nxt = files.open_dir_child(parent_fd, part)
                    os.close(parent_fd)
                    parent_fd = nxt
                now = os.lstat(parts[-1], dir_fd=parent_fd)
                listed: set[str] | None = None
                if kind == "dir":
                    child_fd = files.open_dir_child(parent_fd, parts[-1])
                    try:
                        listed = {n for n in os.listdir(child_fd) if not files.reserved_name(n)}
                    finally:
                        os.close(child_fd)
            finally:
                os.close(parent_fd)
        except (OSError, StorageError):
            return False
        if now.st_ino != info.st_ino:
            return False
        if kind == "file" and (now.st_size != info.st_size or now.st_mtime_ns != info.st_mtime_ns):
            return False
        if listed is not None and listed != expected.get(rel, set()):
            return False
    return True


def cross_root_move(
    ctx: OpCtx,
    node: Node,
    old_parent: Node,
    new_parent: Node,
    *,
    action: str,
    expected_revision: int,
    name: str | None,
    on_conflict: str,
    key: str | None,
    undo_of: uuid.UUID | None = None,
) -> Outcome:
    """ADR-013 §7: copy into the other root (verified byte for byte), then the original goes to the
    trash of the root it came from, with `moved_out` / `moved_in` events that point at each other.
    The new entry has a new identity. Needs `write` on the old and the new parent."""
    del action
    if on_conflict not in ("reject", "keep_both"):
        raise OpError(422, "INVALID_CONFLICT_MODE")
    new_name = _clean(name) if name is not None else node.name
    params = {
        "node": str(node.id),
        "rev": expected_revision,
        "parent": str(new_parent.id),
        "name": new_name,
        "mode": on_conflict,
    }
    done_before = _peek_replay(ctx, "xmove", key, params)
    if done_before is not None:
        return _replayed(ctx, done_before)
    if node.revision != expected_revision:
        raise OpError(409, "REVISION_CONFLICT")
    _usable(old_parent, kind="dir")
    _usable(new_parent, kind="dir")
    _usable(node)
    if _busy_below(ctx.db, node):
        raise OpError(409, "NODE_BUSY")
    src_root, dst_root = _root(ctx, old_parent), _root(ctx, new_parent)
    ensure_trash_base(ctx.settings.library_path)
    entries, size = _subtree_stats(ctx.db, node)
    trash_id = uuid.uuid4()
    now = clock.utcnow()
    purge_after = _deadline(ctx.db, now)
    old_name = node.name
    op, replay = _begin(
        ctx,
        "xmove",
        key,
        node_id=node.id,
        target_parent_id=new_parent.id,
        params=params,
        detail={
            "src_parent": str(old_parent.id),
            "src_name": old_name,
            "dst_parent": str(new_parent.id),
            "dst_name": new_name,
            "trash_id": str(trash_id),
            "source_node": str(node.id),
            "step": "claimed",
            **({"undo_of": str(undo_of)} if undo_of is not None else {}),
        },
    )
    if replay:
        return _replayed(ctx, op)
    src_names = path_components(ctx.db, node)
    dst_comps = path_components(ctx.db, new_parent)
    claimed = ctx.db.execute(
        update(Node)
        .where(Node.id == node.id, Node.revision == expected_revision, Node.status == "active")
        .values(status="busy")
    )
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        err = OpError(409, "REVISION_CONFLICT")
        _record_failure(ctx, op, err)
        raise err
    ctx.db.commit()

    mode = on_conflict
    copied: list[files.TreeEntry] = []
    done = False
    try:
        with ExitStack() as stack:
            src_fd = stack.enter_context(_fs(ctx, src_root, src_names[:-1], write=True))
            dst_fd = stack.enter_context(_fs(ctx, dst_root, dst_comps, write=True))
            info = files.lstat_entry(src_fd, old_name)
            if info is None:
                _set(op, "reconcile_needed", error="CONTENT_MISSING")
                ctx.db.execute(
                    update(Node).where(Node.id == node.id).values(status="reconcile_needed")
                )
                ctx.db.commit()
                raise OpError(409, "CONTENT_MISSING")
            _set(op, "planned", src_ino=info.st_ino, src_dev=info.st_dev, step="copying")
            ctx.db.commit()
            chosen: str | None = None
            plan: list[tuple[tuple[str, ...], str, os.stat_result]] = []
            for cand in _candidates(new_name, node.kind, mode):
                if _db_has(ctx.db, new_parent.id, cand, exclude=node.id):
                    continue
                _note(ctx, op, dst_name=cand, step="copying")
                try:
                    plan, copied = _copy_for_move(ctx, src_fd, old_name, dst_fd, cand)
                except files.NameExists:
                    continue
                chosen = cand
                break
            if chosen is None:
                raise OpError(409, "NAME_CONFLICT")
            _note(ctx, op, dst_name=chosen, step="copied")
            trash_fd = stack.enter_context(_trash_open(ctx, src_root.id, trash_id, create=True))
            source_in_trash = False
            try:
                _note(ctx, op, step="trashing")
                try:
                    files.rename_noreplace(src_fd, old_name, trash_fd, old_name)
                except files.CrossDevice as exc:
                    raise _unavailable() from exc
                source_in_trash = True
                if not _trash_matches_plan(trash_fd, old_name, plan):
                    files.rename_noreplace(trash_fd, old_name, src_fd, old_name)
                    source_in_trash = False
                    raise OpError(409, "SOURCE_CHANGED")
            except BaseException:
                if source_in_trash:
                    # The source could not be put back (a host process took its name, say): keep
                    # the copy too, so neither is lost, and leave it to a person.
                    ctx.db.rollback()
                    current = ctx.db.get(FileOperation, op.id)
                    if current is not None:
                        _set(current, "reconcile_needed", step="source_left_in_trash")
                    ctx.db.execute(
                        update(Node).where(Node.id == node.id).values(status="reconcile_needed")
                    )
                    ctx.db.commit()
                    raise
                if files.remove_created(dst_fd, copied):
                    ctx.db.rollback()
                    current = ctx.db.get(FileOperation, op.id)
                    if current is not None:
                        _set(current, "reconcile_needed", step="copy_cleanup_incomplete")
                        ctx.db.commit()
                raise
            _set(op, "planned", step="filesystem_done")
            try:
                serialize_tree_changes(ctx.db)
                ctx.db.expire_all()
                fresh = ctx.db.get(Node, node.id)
                target = ctx.db.get(Node, new_parent.id)
                if fresh is None or target is None or fresh.status != "busy":
                    raise OpError(409, "PARENT_CHANGED")
                if target.status != "active" or not _chain_idle(ctx.db, target):
                    raise OpError(409, "PARENT_CHANGED")
                top = _insert_copied(ctx, target, copied, op, fresh, moved=True)
                item = _mark_trashed(
                    ctx,
                    fresh,
                    item_id=trash_id,
                    reason="moved_out",
                    trashed_at=now,
                    purge_after=purge_after,
                    entries=entries,
                    size=size,
                    op=op,
                    event="moved_out",
                    event_details={"to_node_id": str(top.id), "to_name": chosen},
                )
                op.node_id = top.id
                undo = (
                    {}
                    if undo_of is not None
                    else {
                        "undo": {
                            "kind": "xmove",
                            "item": str(item.id),
                            "top": str(top.id),
                            "revision": top.revision,
                            "src_revision": expected_revision + 1,
                        }
                    }
                )
                _set(op, "committed", step="complete", **undo)
                audit(
                    ctx.db,
                    "file.xmove",
                    actor=ctx.actor,
                    ip=ctx.ip,
                    node_id=str(node.id),
                    new_node_id=str(top.id),
                    item_id=str(item.id),
                )
                ctx.db.commit()
            except BaseException:
                ctx.db.rollback()
                _undo_xmove(ctx, op, node.id, src_fd, old_name, trash_fd, dst_fd, copied)
                raise
            done = True
    except BaseException as exc:
        err = _map_error(exc)
        if not done:
            ctx.db.rollback()
            current = ctx.db.get(FileOperation, op.id)
            if current is not None and current.status == "planned":
                _set(current, "failed", error=err.code)
            ctx.db.commit()
            _release_claim(ctx, node.id)
            _discard_item_dir(ctx, src_root.id, trash_id)
        if err.status == 500:
            log.exception("cross-root move failed unexpectedly")
        raise err from exc
    result = ctx.db.get(Node, op.node_id) if op.node_id else None
    if result is None:
        raise OpError(500, "OPERATION_FAILED")
    return Outcome(result, op)


def _undo_xmove(
    ctx: OpCtx,
    op: FileOperation,
    node_id: uuid.UUID,
    src_fd: int,
    name: str,
    trash_fd: int,
    dst_fd: int,
    copied: list[files.TreeEntry],
) -> None:
    """The database step failed after the source went to the trash: put it back and remove the
    copy. If either cannot be done, both copies stay and the operation says so."""
    back = removed = False
    try:
        files.rename_noreplace(trash_fd, name, src_fd, name)
        back = True
    except Exception:
        back = False
    if back:
        removed = not files.remove_created(dst_fd, copied)
    ctx.db.rollback()
    current = ctx.db.get(FileOperation, op.id)
    if current is not None:
        if back and removed:
            _set(current, "rolled_back", step="database_failed")
        else:
            _set(current, "reconcile_needed", step="database_failed_filesystem_changed")
    status = "active" if back else "reconcile_needed"
    ctx.db.execute(update(Node).where(Node.id == node_id).values(status=status))
    ctx.db.commit()


# ------------------------------------------------------------------ startup recovery


def _entry_at_trash(ctx: OpCtx, root_id: uuid.UUID, item_id: uuid.UUID, name: str) -> Any:
    """lstat of the entry inside an item's trash directory, or None."""
    try:
        with _trash_open(ctx, root_id, item_id, create=False) as fd:
            return files.lstat_entry(fd, name)
    except (OpError, OSError, StorageError):
        return None


def recover_one(ctx: OpCtx, op: FileOperation) -> str:
    """Resolves one interrupted trash, restore, purge or cross-root operation. Returns the
    outcome name used by `fileops.recover_operations`."""
    d = op.detail or {}
    db = ctx.db
    if op.action == "purge":
        return _recover_purge(ctx, op, d)
    item = db.get(TrashItem, uuid.UUID(d["trash_id"])) if d.get("trash_id") else None
    if op.action == "restore":
        return _recover_restore(ctx, op, d, item)
    node = db.get(Node, op.node_id) if op.node_id else None
    if op.action == "xmove":
        return _recover_xmove(ctx, op, d, node)
    # trash
    if node is None or node.status != "busy" or "src_ino" not in d or item is not None:
        if node is not None and node.status == "busy":
            db.execute(update(Node).where(Node.id == node.id).values(status="active"))
        _set(op, "failed", error="INTERRUPTED")
        return "failed"
    src_parent = db.get(Node, uuid.UUID(d["src_parent"]))
    if src_parent is None:
        return _flag_node(db, op, node)
    root_id = node.root_id
    here = _stat_at(ctx, src_parent, d["src_name"])
    there = _entry_at_trash(ctx, root_id, uuid.UUID(d["trash_id"]), d["src_name"])
    ino = d["src_ino"]
    if here is not None and here.st_ino == ino and there is None:
        db.execute(update(Node).where(Node.id == node.id).values(status="active"))
        _set(op, "rolled_back", step="recovered_untouched")
        return "rolled_back"
    if here is None and there is not None and there.st_ino == ino:
        entries, size = _subtree_stats(db, node)
        purge_after = datetime.fromisoformat(d["purge_after"]) if d.get("purge_after") else None
        _mark_trashed(
            ctx,
            node,
            item_id=uuid.UUID(d["trash_id"]),
            reason=d.get("reason", "deleted"),
            trashed_at=clock.utcnow(),
            purge_after=purge_after,
            entries=entries,
            size=size,
            op=op,
            event="trashed",
            event_details={"recovered": True},
        )
        _set(op, "committed", step="recovered_forward")
        return "committed"
    return _flag_node(db, op, node)


def release_orphan_items(db: Session) -> int:
    """A restore or purge claims its item (`busy`) before it journals the operation; a crash in
    between leaves an item that nothing would ever release. With no operation in flight for it,
    nothing touched its bytes: it goes back to the trash, or to `reconcile_needed` when its nodes
    no longer say they are held by it. Called at the end of startup recovery."""
    in_flight = {
        str((op.detail or {}).get("trash_id"))
        for op in db.scalars(
            select(FileOperation).where(
                FileOperation.status == "planned",
                FileOperation.action.in_(("restore", "purge")),
            )
        )
    }
    released = 0
    for item in db.scalars(select(TrashItem).where(TrashItem.status == "busy")):
        if str(item.id) in in_flight:
            continue
        node = db.get(Node, item.node_id)
        held = node is not None and node.trash_item_id == item.id and node.status == "trashed"
        item.status = "in_trash" if held else "reconcile_needed"
        released += 1
    return released


@dataclass
class CheckResult:
    item: TrashItem
    released: bool
    reason: str | None  # entry_missing | entry_changed | node_moved_on


def check_release(ctx: OpCtx, item_id: uuid.UUID) -> CheckResult:
    """The "check and release" action for an item flagged `reconcile_needed` (ADR-017 addendum).
    Looks again at the bytes in the trash and at the node row. If both still match what was trashed,
    the item goes back to `in_trash` (and can be restored or deleted as usual). Otherwise it stays
    flagged and the answer says why: `entry_missing`, `entry_changed` or `node_moved_on`. Nothing is
    deleted, moved or renamed by this action. One event and one audit row either way."""
    db = ctx.db
    item = db.get(TrashItem, item_id)
    if item is None or not item_visible(db, ctx.actor, item, ctx.session_id):
        audit(db, "access.denied", actor=ctx.actor, ip=ctx.ip, item_id=str(item_id))
        db.commit()
        raise OpError(403, "FORBIDDEN")
    return _check_item(ctx, item)


def check_release_root(ctx: OpCtx, root_node: Node) -> dict[str, int]:
    """The same check for every flagged item of one root, for the root's manager (the owner of a
    personal folder, an administrator for the shared tree), who may not be able to see each item."""
    if not can_manage_root(ctx.db, ctx.actor, root_node, ctx.session_id):
        audit(ctx.db, "access.denied", actor=ctx.actor, ip=ctx.ip, node_id=str(root_node.id))
        ctx.db.commit()
        raise OpError(403, "FORBIDDEN")
    flagged = list(
        ctx.db.scalars(
            select(TrashItem).where(
                TrashItem.root_id == root_node.root_id, TrashItem.status == "reconcile_needed"
            )
        )
    )
    released = 0
    for item in flagged:
        try:
            if _check_item(ctx, item).released:
                released += 1
        except OpError:
            continue  # claimed by a delete forever meanwhile: not ours to touch
    orphans = orphan_operations(ctx.db, root_node.root_id)
    adopted = sum(1 for op in orphans if _adopt_orphan(ctx, op))
    checked = len(flagged) + len(orphans)
    return {
        "checked": checked,
        "released": released + adopted,
        "still_flagged": checked - released - adopted,
    }


def _operation_running(db: Session, item_id: uuid.UUID) -> bool:
    """A restore or purge of this item is journaled and unfinished."""
    for op in db.scalars(
        select(FileOperation).where(
            FileOperation.status == "planned", FileOperation.action.in_(("restore", "purge"))
        )
    ):
        if (op.detail or {}).get("trash_id") == str(item_id):
            return True
    return False


def _tree_matches(ctx: OpCtx, item: TrashItem) -> bool:
    """A folder item is intact when the item directory holds exactly the entries (names, kinds,
    file sizes) of the nodes that belong to the item. Bounded by the tree limits."""
    rows = ctx.db.execute(
        select(Node.id, Node.parent_id, Node.name, Node.kind, Node.size).where(
            Node.trash_item_id == item.id
        )
    ).all()
    by_id = {r.id: r for r in rows}
    expected: dict[tuple[str, ...], tuple[str, int | None]] = {}
    for r in rows:
        rel: list[str] = []
        cur = r
        while True:
            rel.append(cur.name)
            if cur.id == item.node_id:
                break
            nxt = by_id.get(cur.parent_id)
            if nxt is None:
                return False
            cur = nxt
        expected[tuple(reversed(rel))] = (r.kind, r.size if r.kind == "file" else 0)
    try:
        with _trash_open(ctx, item.root_id, item.id, create=False) as fd:
            found = files.scan_tree(
                fd,
                item.original_name,
                max_entries=ctx.settings.max_tree_entries,
                max_depth=ctx.settings.max_tree_depth,
            )
    except (OpError, OSError, StorageError):
        return False
    if len(found) != len(expected):
        return False
    for rel_path, kind, info in found:
        want = expected.get(rel_path)
        if want is None or want[0] != kind:
            return False
        if kind == "file" and want[1] is not None and want[1] != info.st_size:
            return False
    return True


def _check_item(ctx: OpCtx, item: TrashItem) -> CheckResult:
    db = ctx.db
    if item.status != "reconcile_needed" or _operation_running(db, item.id):
        raise OpError(409, "TRASH_ITEM_UNAVAILABLE")
    node = db.get(Node, item.node_id)
    reason: str | None = None
    if (
        node is None
        or node.trash_item_id != item.id
        or node.status
        not in (
            "trashed",
            "reconcile_needed",
        )
    ):
        reason = "node_moved_on"
    else:
        entry = _entry_at_trash(ctx, item.root_id, item.id, item.original_name)
        if entry is None:
            reason = "entry_missing"
        elif (item.kind == "dir") != stat.S_ISDIR(entry.st_mode) or (
            item.kind == "file"
            and (
                not stat.S_ISREG(entry.st_mode)
                or (node.size is not None and entry.st_size != node.size)
            )
        ):
            reason = "entry_changed"
        elif item.kind == "dir" and not _tree_matches(ctx, item):
            reason = "entry_changed"
    if reason is None:
        assert node is not None  # noqa: S101
        # Compare-and-swap: a delete forever may have claimed the item since it was read.
        swapped = db.execute(
            update(TrashItem)
            .where(TrashItem.id == item.id, TrashItem.status == "reconcile_needed")
            .values(status="in_trash")
        )
        if swapped.rowcount != 1:  # type: ignore[attr-defined]
            db.rollback()
            raise OpError(409, "TRASH_ITEM_UNAVAILABLE")
        if node.status != "trashed":
            db.execute(update(Node).where(Node.id == node.id).values(status="trashed"))
    events.record(
        db,
        item.node_id,
        "checked",
        **ctx.event_actor(),
        name=item.original_name,
        trash_item_id=str(item.id),
        how="released" if reason is None else reason,
    )
    audit(
        db,
        "trash.checked",
        actor=ctx.actor,
        ip=ctx.ip,
        item_id=str(item.id),
        result="released" if reason is None else reason,
    )
    db.commit()
    db.refresh(item)
    return CheckResult(item=item, released=reason is None, reason=reason)


def orphan_operations(db: Session, root_id: uuid.UUID) -> list[FileOperation]:
    """Trash or cross-root-move operations left `reconcile_needed` whose bytes may sit in the
    trash directory without a Trash entry (the source could not be put back, or the database step
    failed after the rename)."""
    found: list[FileOperation] = []
    ops = db.scalars(
        select(FileOperation).where(
            FileOperation.status == "reconcile_needed",
            FileOperation.action.in_(("trash", "xmove")),
        )
    )
    for op in ops:
        d = op.detail or {}
        if not d.get("trash_id") or "src_ino" not in d or not op.node_id:
            continue
        node = db.get(Node, op.node_id)
        if node is None or node.root_id != root_id or node.status != "reconcile_needed":
            continue
        if db.get(TrashItem, uuid.UUID(d["trash_id"])) is not None:
            continue
        found.append(op)
    return found


def _adopt_orphan(ctx: OpCtx, op: FileOperation) -> bool:
    """Gives the bytes of an interrupted trash or move a Trash entry, when the journal's inode
    still matches what is in the trash directory. Otherwise leaves everything as it is."""
    db = ctx.db
    d = op.detail or {}
    node = db.get(Node, op.node_id) if op.node_id else None
    if node is None or node.parent_id is None:
        return False
    trash_id = uuid.UUID(d["trash_id"])
    entry = _entry_at_trash(ctx, node.root_id, trash_id, d.get("src_name", node.name))
    if entry is None or entry.st_ino != d["src_ino"]:
        return False
    now = clock.utcnow()
    purge_after = (
        datetime.fromisoformat(d["purge_after"]) if d.get("purge_after") else _deadline(db, now)
    )
    entries, size = _subtree_stats(db, node)
    serialize_tree_changes(db)
    db.expire_all()
    fresh = db.get(Node, node.id)
    if fresh is None or fresh.status != "reconcile_needed":
        return False
    _mark_trashed(
        ctx,
        fresh,
        item_id=trash_id,
        reason="moved_out" if op.action == "xmove" else d.get("reason", "deleted"),
        trashed_at=now,
        purge_after=purge_after,
        entries=entries,
        size=size,
        op=op,
        event="trashed",
        event_details={"adopted": True},
        trashed_by=op.actor_user_id,
    )
    current = db.get(FileOperation, op.id)
    if current is not None:
        _set(current, "committed", step="adopted_into_trash")
    audit(
        db,
        "trash.adopted",
        actor=ctx.actor,
        ip=ctx.ip,
        item_id=str(trash_id),
    )
    db.commit()
    return True


def _flag_node(db: Session, op: FileOperation, node: Node) -> str:
    db.execute(update(Node).where(Node.id == node.id).values(status="reconcile_needed"))
    _set(op, "reconcile_needed", step="recovery_undecided")
    return "reconcile_needed"


def _recover_restore(
    ctx: OpCtx, op: FileOperation, d: dict[str, Any], item: TrashItem | None
) -> str:
    db = ctx.db
    node = db.get(Node, op.node_id) if op.node_id else None
    dest = db.get(Node, uuid.UUID(d["dst_parent"])) if d.get("dst_parent") else None
    if item is None or node is None or dest is None or "src_ino" not in d:
        if item is not None and item.status == "busy":
            item.status = "in_trash"
        _set(op, "failed", error="INTERRUPTED")
        return "failed"
    there = _entry_at_trash(ctx, item.root_id, item.id, item.original_name)
    here = _stat_at(ctx, dest, d["dst_name"])
    ino = d["src_ino"]
    if there is not None and there.st_ino == ino and (here is None or here.st_ino != ino):
        item.status = "in_trash"
        _set(op, "rolled_back", step="recovered_untouched")
        return "rolled_back"
    if there is None and here is not None and here.st_ino == ino and node.status == "trashed":
        _apply_restore(ctx, node, dest, d["dst_name"], item.id)
        item.status, item.finished_at = "restored", clock.utcnow()
        events.record(
            db,
            node.id,
            "restored",
            correlation_id=op.id,
            **ctx.event_actor(),
            old_name=item.original_name,
            name=d["dst_name"],
            to_parent_id=str(dest.id),
            trash_item_id=str(item.id),
            recovered=True,
        )
        _set(op, "committed", step="recovered_forward")
        return "committed"
    item.status = "reconcile_needed"
    _set(op, "reconcile_needed", step="recovery_undecided")
    return "reconcile_needed"


def _recover_purge(ctx: OpCtx, op: FileOperation, d: dict[str, Any]) -> str:
    db = ctx.db
    item = db.get(TrashItem, uuid.UUID(d["trash_id"])) if d.get("trash_id") else None
    if item is None:
        _set(op, "failed", error="INTERRUPTED")
        return "failed"
    if d.get("step") != "deleting":
        item.status = d.get("prior", "in_trash")  # the deletion never began
        _set(op, "rolled_back", step="recovered_untouched")
        return "rolled_back"
    if _entry_at_trash(ctx, item.root_id, item.id, item.original_name) is None and not _dir_exists(
        ctx, item
    ):
        now = clock.utcnow()
        db.execute(
            update(Node)
            .where(Node.trash_item_id == item.id)
            .values(status="purged", updated_at=now)
            .execution_options(synchronize_session=False)
        )
        item.status, item.finished_at = "purged", now
        events.record(
            db,
            item.node_id,
            "purged",
            correlation_id=op.id,
            actor_kind=events.SYSTEM,
            source="system",
            name=item.original_name,
            trash_item_id=str(item.id),
            entries=item.entry_count,
            size=item.size_bytes,
            how="recovered",
        )
        _set(op, "committed", step="recovered_forward")
        return "committed"
    item.status = "reconcile_needed"  # partly deleted: a person decides
    _set(op, "reconcile_needed", step="recovery_undecided")
    return "reconcile_needed"


def _dir_exists(ctx: OpCtx, item: TrashItem) -> bool:
    try:
        with _trash_open(ctx, item.root_id, item.id, create=False):
            return True
    except (OpError, OSError, StorageError):
        return False


def _recover_xmove(ctx: OpCtx, op: FileOperation, d: dict[str, Any], node: Node | None) -> str:
    """Never loses a file. If the source is still in place the node is released; if it already
    went to the trash it is moved back. A copy at the destination is left for the scanner to adopt
    and the operation says `reconcile_needed` so a person looks."""
    db = ctx.db
    if node is None or node.status != "busy" or "src_ino" not in d:
        if node is not None and node.status == "busy":
            db.execute(update(Node).where(Node.id == node.id).values(status="active"))
        _set(op, "failed", error="INTERRUPTED")
        return "failed"
    src_parent = db.get(Node, uuid.UUID(d["src_parent"]))
    dst_parent = db.get(Node, uuid.UUID(d["dst_parent"]))
    if src_parent is None or dst_parent is None:
        return _flag_node(db, op, node)
    trash_id = uuid.UUID(d["trash_id"])
    ino = d["src_ino"]
    here = _stat_at(ctx, src_parent, d["src_name"])
    in_trash = _entry_at_trash(ctx, node.root_id, trash_id, d["src_name"])
    copy_there = bool(d.get("dst_name")) and _stat_at(ctx, dst_parent, d["dst_name"]) is not None
    if in_trash is not None and in_trash.st_ino == ino and here is None:
        try:
            root = db.get(StorageRoot, node.root_id)
            assert root is not None  # noqa: S101
            with (
                _trash_open(ctx, node.root_id, trash_id, create=False) as tfd,
                open_dir(
                    ctx.settings.library_path, root.relative_dir, path_components(db, src_parent)
                ) as sfd,
            ):
                files.rename_noreplace(tfd, d["src_name"], sfd, d["src_name"])
        except Exception:
            return _flag_node(db, op, node)
        here = _stat_at(ctx, src_parent, d["src_name"])
    if here is not None and here.st_ino == ino:
        db.execute(update(Node).where(Node.id == node.id).values(status="active"))
        if copy_there:
            _set(op, "reconcile_needed", step="copy_left_at_destination")
            return "reconcile_needed"
        _set(op, "rolled_back", step="recovered_untouched")
        return "rolled_back"
    return _flag_node(db, op, node)
