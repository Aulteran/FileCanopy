"""Undo for the operations that can be reversed safely (M07 plan, acceptance item 4).

Each reversible operation records its preconditions in `file_operation.detail["undo"]` when it
commits. Undo re-checks them against the current state and refuses with a reason code when anything
relevant changed, so an old undo can never overwrite newer work: `changed` (the entry was modified
or renamed since), `moved` (it is somewhere else now), `name_taken` (the original name is in use),
`missing` (it, or the place it should go back to, is gone) or `no_access` (the person lost the
right). Undo has no time limit; the preconditions decide.

Undo of a copy or an import batch moves what it created to the trash and leaves anything that
changed since (or was added later) where it is, reporting it.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import clock
from app.auth.service import audit
from app.db.models import (
    HIDDEN_NODE_STATUSES,
    FileEvent,
    FileOperation,
    ImportBatch,
    Node,
    TrashItem,
)
from app.domain import fileops, trash
from app.domain.fileops import OpCtx, OpError, _db_has, _stat_at
from app.policy import engine
from app.policy.model import Actor, Privilege

CONFLICT_REASONS = ("changed", "moved", "name_taken", "missing", "no_access")
_CODE_TO_REASON = {
    "NAME_CONFLICT": "name_taken",
    "REVISION_CONFLICT": "changed",
    "SOURCE_CHANGED": "changed",
    "NODE_BUSY": "changed",
    "CONTENT_MISSING": "missing",
    "PARENT_UNAVAILABLE": "missing",
    "PARENT_CHANGED": "missing",
    "TRASH_ITEM_UNAVAILABLE": "missing",
    "ALREADY_TRASHED": "missing",
    "FORBIDDEN": "no_access",
}


class UndoConflict(Exception):
    """A recorded precondition no longer holds; nothing was changed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        assert reason in CONFLICT_REASONS  # noqa: S101
        self.reason = reason


@dataclass
class Left:
    node_id: uuid.UUID
    name: str
    reason: str


@dataclass
class UndoResult:
    op: FileOperation | None
    node: Node | None = None
    trashed: int = 0
    left_in_place: list[Left] = field(default_factory=list)
    replay: bool = False


def _conflict(err: OpError) -> Exception:
    reason = _CODE_TO_REASON.get(err.code)
    return UndoConflict(reason) if reason is not None else err


def _live(db: Session, node_id: uuid.UUID | str | None) -> Node | None:
    if node_id is None:
        return None
    node = db.get(Node, uuid.UUID(str(node_id)))
    return node if node is not None and node.status not in HIDDEN_NODE_STATUSES else None


def _can_write(ctx: OpCtx, node: Node) -> bool:
    return Privilege.WRITE in engine.effective(ctx.db, Actor(ctx.actor, session_id=ctx.session_id), node)


# ------------------------------------------------------------------ entry point


def undo_operation(ctx: OpCtx, operation_id: uuid.UUID, key: str | None) -> UndoResult:
    """Reverses one of the caller's own committed operations. Raises UndoConflict (409
    UNDO_CONFLICT with a reason) when a precondition fails, OpError(409, NOT_REVERSIBLE) for an
    operation that cannot be undone or was already undone."""
    op = fileops.get_operation(ctx.db, ctx.actor, operation_id)
    if op is None:
        raise OpError(403, "FORBIDDEN")
    detail = dict(op.detail or {})
    if "undone_by" in detail:
        again = ctx.db.get(FileOperation, uuid.UUID(detail["undone_by"]))
        if key is not None and again is not None and again.idempotency_key == key:
            return UndoResult(again, replay=True)  # the same request, repeated
        raise OpError(409, "NOT_REVERSIBLE")
    spec = detail.get("undo")
    if op.status != "committed" or not isinstance(spec, dict):
        raise OpError(409, "NOT_REVERSIBLE")
    handlers = {
        "relocate": _undo_relocate,
        "trash": _undo_trash,
        "copy": _undo_copy,
        "xmove": _undo_xmove,
    }
    handler = handlers.get(spec.get("kind", ""))
    if handler is None:
        raise OpError(409, "NOT_REVERSIBLE")
    try:
        result: UndoResult = handler(ctx, op, spec, key)
    except OpError as err:
        raise _conflict(err) from err
    _mark_undone(ctx, op.id, result.op)
    audit(
        ctx.db,
        "file.undo",
        actor=ctx.actor,
        ip=ctx.ip,
        operation_id=str(op.id),
        kind=spec.get("kind"),
        trashed=result.trashed,
    )
    ctx.db.commit()
    return result


def _mark_undone(ctx: OpCtx, operation_id: uuid.UUID, by: FileOperation | None) -> None:
    op = ctx.db.get(FileOperation, operation_id)
    if op is None or by is None:
        return
    op.detail = {**(op.detail or {}), "undone_by": str(by.id)}
    op.updated_at = clock.utcnow()
    ctx.db.flush()


# ------------------------------------------------------------------ rename and same-root move


def _undo_relocate(
    ctx: OpCtx, op: FileOperation, spec: dict[str, Any], key: str | None
) -> UndoResult:
    db = ctx.db
    node = db.get(Node, op.node_id) if op.node_id else None
    if node is None or node.status in HIDDEN_NODE_STATUSES or node.status == "missing":
        raise UndoConflict("missing")
    src_parent = _live(db, spec["src_parent"])
    if src_parent is None or src_parent.status != "active":
        raise UndoConflict("missing")
    if node.status != "active" or node.parent_id is None:
        raise UndoConflict("changed")
    if node.revision != spec["revision"]:
        here = node.parent_id != uuid.UUID(spec["dst_parent"]) or node.name != spec["dst_name"]
        raise UndoConflict("moved" if here else "changed")
    current = db.get(Node, node.parent_id)
    if current is None or not _can_write(ctx, current) or not _can_write(ctx, src_parent):
        raise UndoConflict("no_access")
    if _db_has(db, src_parent.id, spec["src_name"], exclude=node.id):
        raise UndoConflict("name_taken")
    on_disk = _stat_at(ctx, current, node.name)
    if on_disk is None:
        raise UndoConflict("missing")
    if _disk_changed(node, on_disk):
        raise UndoConflict("changed")
    outcome = fileops.relocate(
        ctx,
        node.id,
        action=spec["action"],
        expected_revision=node.revision,
        target_parent_id=src_parent.id,
        name=spec["src_name"],
        on_conflict="reject",
        key=key,
        undo_of=op.id,
    )
    return UndoResult(outcome.op, outcome.node)


def _disk_changed(node: Node, on_disk: Any) -> bool:
    """The entry on disk is no longer the one the database last saw (edited or swapped outside)."""
    if node.fs_ino is not None and on_disk.st_ino != node.fs_ino:
        return True
    if node.kind == "file":
        if node.size is not None and on_disk.st_size != node.size:
            return True
        if node.mtime_ns is not None and on_disk.st_mtime_ns != node.mtime_ns:
            return True
    return False


# ------------------------------------------------------------------ trash


def _undo_trash(ctx: OpCtx, op: FileOperation, spec: dict[str, Any], key: str | None) -> UndoResult:
    db = ctx.db
    item = db.get(TrashItem, uuid.UUID(spec["item"]))
    if item is None or item.status != "in_trash":
        raise UndoConflict("missing")  # restored, purged or being handled already
    node = db.get(Node, item.node_id)
    if node is None or node.revision != spec["revision"]:
        raise UndoConflict("changed")
    parent = _live(db, item.original_parent_id)
    if parent is None or parent.status != "active":
        raise UndoConflict("missing")
    if not _can_write(ctx, parent):
        raise UndoConflict("no_access")
    if _db_has(db, parent.id, item.original_name):
        raise UndoConflict("name_taken")
    outcome = trash.restore(
        ctx, item.id, target_parent_id=None, on_conflict="fail", key=key, undo_of=op.id
    )
    return UndoResult(outcome.op, outcome.node)


# ------------------------------------------------------------------ copy, import, cross-root move


def _created_ids(
    db: Session, correlation_id: uuid.UUID, actions: tuple[str, ...]
) -> set[uuid.UUID]:
    return set(
        db.scalars(
            select(FileEvent.node_id).where(
                FileEvent.correlation_id == correlation_id, FileEvent.action.in_(actions)
            )
        )
    )


def _load_nodes(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, Node]:
    out: dict[uuid.UUID, Node] = {}
    pending = sorted(ids)
    for start in range(0, len(pending), 500):
        for node in db.scalars(select(Node).where(Node.id.in_(pending[start : start + 500]))):
            out[node.id] = node
    return out


def _plan_cleanup(ctx: OpCtx, created: set[uuid.UUID]) -> tuple[list[Node], list[tuple[Node, str]]]:
    """Which of the entries an operation created can go to the trash: the largest subtrees made
    only of entries that are still unchanged (revision untouched, same file on disk). Anything
    else is reported and left in place."""
    db = ctx.db
    nodes = _load_nodes(db, created)
    live = {i: n for i, n in nodes.items() if n.status not in HIDDEN_NODE_STATUSES}
    roots = [n for n in live.values() if n.parent_id not in live]
    children: dict[uuid.UUID, list[Node]] = {}
    for root in roots:
        for node in db.scalars(
            select(Node).where(
                Node.lineage.startswith(root.lineage, autoescape=True),
                Node.id != root.id,
                Node.status.not_in(HIDDEN_NODE_STATUSES),
            )
        ):
            if node.parent_id is not None:
                children.setdefault(node.parent_id, []).append(node)
    memo: dict[uuid.UUID, bool] = {}

    def unchanged(node: Node) -> bool:
        if node.id not in live or node.status != "active" or node.revision != 1:
            return False
        if node.kind != "file" or node.parent_id is None or node.fs_ino is None:
            return True
        parent = db.get(Node, node.parent_id)
        on_disk = _stat_at(ctx, parent, node.name) if parent is not None else None
        return on_disk is not None and not _disk_changed(node, on_disk)

    def clean(node: Node) -> bool:
        if node.id not in memo:
            memo[node.id] = unchanged(node) and all(clean(c) for c in children.get(node.id, []))
        return memo[node.id]

    take: list[Node] = []
    left: list[tuple[Node, str]] = []

    def visit(node: Node) -> None:
        if clean(node):
            take.append(node)
            return
        if node.id not in live:
            left.append((node, "added_later"))
        elif not unchanged(node):
            left.append((node, "changed"))
        else:
            left.append((node, "contains_changes"))
        for child in children.get(node.id, []):
            visit(child)

    for root in sorted(roots, key=lambda n: n.name):
        visit(root)
    return take, left


def _trash_created(
    ctx: OpCtx,
    op_id: uuid.UUID,
    created: set[uuid.UUID],
    *,
    reason: str,
    key: str | None,
    extra: dict[str, Any],
) -> UndoResult:
    take, left_nodes = _plan_cleanup(ctx, created)
    left = [Left(n.id, n.name, why) for n, why in left_nodes]
    if not take:
        raise UndoConflict("missing" if not left else "changed")
    first: FileOperation | None = None
    trashed = 0
    for node in take:
        try:
            outcome = trash.trash(
                ctx,
                node.id,
                expected_revision=node.revision,
                key=key if len(take) == 1 else None,
                reason=reason,
                undo_of=op_id,
                event="undone",
                event_details={"operation_id": str(op_id), **extra},
            )
        except OpError as err:
            if first is None and len(take) == 1:
                raise
            left.append(Left(node.id, node.name, _CODE_TO_REASON.get(err.code, "changed")))
            continue
        trashed += outcome.item.entry_count
        first = first or outcome.op
    if first is None:
        raise UndoConflict("no_access" if any(x.reason == "no_access" for x in left) else "changed")
    return UndoResult(first, None, trashed=trashed, left_in_place=left)


def _undo_copy(ctx: OpCtx, op: FileOperation, spec: dict[str, Any], key: str | None) -> UndoResult:
    created = _created_ids(ctx.db, op.id, ("copied", "created"))
    top = _live(ctx.db, spec["top"])
    if top is None:
        raise UndoConflict("missing")
    return _trash_created(ctx, op.id, created, reason="copy_undone", key=key, extra={})


def undo_import(ctx: OpCtx, batch: ImportBatch, key: str | None = None) -> UndoResult:
    """Moves the entries an import batch created to the trash (reason `import_undone`), skipping
    and reporting anything that changed since. Only the person who ran the batch (the route's
    guard); each entry is still authorized when it is trashed."""
    if batch.actor_user_id != ctx.actor:
        raise OpError(403, "FORBIDDEN")
    if batch.status in ("planned", "running"):
        raise OpError(409, "BATCH_NOT_FINISHED")
    created = _created_ids(ctx.db, batch.id, ("imported",))
    if not created:
        raise OpError(409, "NOT_REVERSIBLE")
    try:
        result = _trash_created(
            ctx,
            batch.id,
            created,
            reason="import_undone",
            key=key,
            extra={"batch_id": str(batch.id)},
        )
    except OpError as err:
        raise _conflict(err) from err
    plan = dict(batch.plan) if isinstance(batch.plan, dict) else {}
    plan["undone"] = {
        "at": clock.utcnow().isoformat(),
        "trashed": result.trashed,
        "left": len(result.left_in_place),
    }
    batch.plan = plan
    audit(
        ctx.db,
        "import.undo",
        actor=ctx.actor,
        ip=ctx.ip,
        batch_id=str(batch.id),
        trashed=result.trashed,
        left=len(result.left_in_place),
    )
    ctx.db.commit()
    return result


def _undo_xmove(ctx: OpCtx, op: FileOperation, spec: dict[str, Any], key: str | None) -> UndoResult:
    """Restore the original from the source root's trash, then trash the copy. The original comes
    back first, so a failure in between leaves two copies, never none."""
    db = ctx.db
    item = db.get(TrashItem, uuid.UUID(spec["item"]))
    if item is None or item.status != "in_trash":
        raise UndoConflict("missing")
    source = db.get(Node, item.node_id)
    if source is None or source.revision != spec["src_revision"]:
        raise UndoConflict("changed")
    top = _live(db, spec["top"])
    if top is None:
        raise UndoConflict("missing")
    if top.revision != spec["revision"] or top.status != "active":
        raise UndoConflict("changed")
    parent = _live(db, item.original_parent_id)
    if parent is None or parent.status != "active":
        raise UndoConflict("missing")
    top_parent = db.get(Node, top.parent_id) if top.parent_id else None
    if top_parent is None or not _can_write(ctx, top_parent) or not _can_write(ctx, parent):
        raise UndoConflict("no_access")
    if _db_has(db, parent.id, item.original_name):
        raise UndoConflict("name_taken")
    created = _created_ids(db, op.id, ("moved_in", "created"))
    take, left_nodes = _plan_cleanup(ctx, created)
    if len(take) != 1 or take[0].id != top.id or left_nodes:
        raise UndoConflict("changed")  # the copy was edited or added to: never trash that
    restored = trash.restore(
        ctx, item.id, target_parent_id=None, on_conflict="fail", key=key, undo_of=op.id
    )
    left: list[Left] = []
    trashed = 0
    try:
        outcome = trash.trash(
            ctx,
            top.id,
            expected_revision=top.revision,
            key=None,
            reason="copy_undone",
            undo_of=op.id,
            event="undone",
            event_details={"operation_id": str(op.id)},
        )
        trashed = outcome.item.entry_count
    except OpError as err:
        left.append(Left(top.id, top.name, _CODE_TO_REASON.get(err.code, "changed")))
    return UndoResult(restored.op, restored.node, trashed=trashed, left_in_place=left)


# ------------------------------------------------------------------ recent actions


def reversible(op: FileOperation) -> bool:
    detail = op.detail or {}
    return (
        op.status == "committed"
        and isinstance(detail.get("undo"), dict)
        and ("undone_by" not in detail)
    )
