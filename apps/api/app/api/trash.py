"""Trash, activity history and undo routes (M07, ADR-017). Ids only: no endpoint accepts a path and
none returns a host path. Every route depends on a policy guard; trash items are visible per
ADR-017 §3, so other people's and nonexistent ids look the same."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.files import (
    IdempotencyKey,
    OperationOut,
    ResultOut,
    _child,
    _ctx,
    _http,
    _operation,
    _result,
    operation_access,
)
from app.auth import clock
from app.auth.deps import Principal, get_db, http_error
from app.db.models import FileOperation, ImportBatch, Node, StorageRoot, TrashItem, User
from app.domain import activity, trash, undo
from app.domain.fileops import OpError
from app.domain.nodes import root_node
from app.policy import engine
from app.policy.deps import (
    NodeContext,
    actor_access,
    grant_admin_access,
    read_access,
    trash_access,
    trash_item_access,
)
from app.policy.model import Actor, Privilege

router = APIRouter(prefix="/api/v1", tags=["trash"])


# ------------------------------------------------------------------ models


class TrashItemOut(BaseModel):
    id: uuid.UUID
    root_node_id: uuid.UUID | None
    name: str
    kind: str
    size_bytes: int
    entry_count: int
    trashed_by_name: str | None
    trashed_at: datetime
    reason: str
    purge_after: datetime | None  # null: automatic deletion is switched off
    days_left: int | None
    expiring: bool  # inside the warning window (the last 7 days)
    status: str
    # The folder it came from, only while the caller can still see it.
    original_parent_id: uuid.UUID | None
    original_parent_name: str | None
    confirm_token: str


class TrashRootOut(BaseModel):
    root_node_id: uuid.UUID
    kind: str
    item_count: int
    total_size: int
    empty_token: str
    flagged_count: int  # interrupted items that need "check and release"


class CheckRootOut(BaseModel):
    checked: int
    released: int
    still_flagged: int


class TrashListOut(BaseModel):
    items: list[TrashItemOut]
    next_after: str | None
    roots: list[TrashRootOut]  # roots the caller may empty
    retention_days: int | None


class RestoreBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_parent_id: uuid.UUID | None = None
    on_conflict: Literal["keep_both", "fail"] = "keep_both"


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: str = Field(min_length=1, max_length=64)


class EmptyOut(BaseModel):
    purged: int
    failed: int
    bytes: int


class CheckOut(BaseModel):
    released: bool
    reason: Literal["entry_missing", "entry_changed", "node_moved_on"] | None
    item: TrashItemOut


class TrashedOut(BaseModel):
    item: TrashItemOut
    operation: OperationOut | None
    replayed: bool = False


class EventModel(BaseModel):
    id: int
    node_id: uuid.UUID
    at: datetime
    action: str
    actor_kind: str
    actor_name: str | None
    source: str
    details: dict[str, object]


class ActivityOut(BaseModel):
    items: list[EventModel]
    next_before: int | None


class LeftOut(BaseModel):
    node_id: uuid.UUID
    name: str
    reason: str


class UndoOut(BaseModel):
    operation: OperationOut | None
    node: object | None
    trashed: int = 0
    left_in_place: list[LeftOut] = []
    replayed: bool = False


class RecentOut(BaseModel):
    id: uuid.UUID
    kind: Literal["operation", "import"]
    action: str
    name: str | None
    at: datetime
    node_id: uuid.UUID | None  # only while the caller can still see it
    reversible: bool
    undone: bool


# ------------------------------------------------------------------ helpers


def _item_out(db: Session, actor_id: uuid.UUID, item: TrashItem) -> TrashItemOut:
    now = clock.utcnow()
    days_left: int | None = None
    if item.purge_after is not None:
        days_left = max(0, (item.purge_after - now).days)
    parent = db.get(Node, item.original_parent_id)
    parent_visible = (
        parent is not None
        and parent.status == "active"
        and Privilege.TRAVERSE in engine.effective(db, Actor(actor_id), parent)
    )
    who = db.get(User, item.trashed_by)
    root = root_node(db, item.root_id)
    return TrashItemOut(
        id=item.id,
        root_node_id=root.id,
        name=item.original_name,
        kind=item.kind,
        size_bytes=item.size_bytes,
        entry_count=item.entry_count,
        trashed_by_name=who.name if who else None,
        trashed_at=item.trashed_at,
        reason=item.reason,
        purge_after=item.purge_after,
        days_left=days_left,
        expiring=days_left is not None and days_left <= trash.WARNING_DAYS,
        status=item.status,
        original_parent_id=parent.id if parent is not None and parent_visible else None,
        original_parent_name=parent.name if parent is not None and parent_visible else None,
        confirm_token=trash.confirm_token(item),
    )


def _undo_conflict(reason: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "UNDO_CONFLICT", "reason": reason})


# ------------------------------------------------------------------ trash


@router.delete("/nodes/{node_id}", response_model=TrashedOut)
def delete_node(
    request: Request,
    expected_revision: int = Query(ge=1),
    ctx: NodeContext = Depends(trash_access),
    db: Session = Depends(get_db),
    idempotency_key: str | None = IdempotencyKey,
) -> TrashedOut:
    """Moves the file or folder (and everything in it) to the trash. Never a hard delete."""
    try:
        out = trash.trash(
            _ctx(request, ctx.principal, db),
            ctx.node.id,
            expected_revision=expected_revision,
            key=idempotency_key,
        )
    except OpError as err:
        raise _http(err) from err
    return TrashedOut(
        item=_item_out(db, ctx.principal.user.id, out.item),
        operation=_operation(out.op),
        replayed=out.replay,
    )


@router.get("/trash", response_model=TrashListOut)
def list_trash(
    principal: Principal = Depends(actor_access),
    db: Session = Depends(get_db),
    root_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: int = Query(default=100, ge=1, le=200),
    after: str | None = Query(default=None, max_length=100),
) -> TrashListOut:
    """The caller's visible trash, newest first. `root_id` is the id of a root node as listed by
    `/roots`. `after` is the `next_after` of the previous page."""
    actor_id = principal.user.id
    storage_root: uuid.UUID | None = None
    if root_id is not None:
        node = db.get(Node, root_id)
        if node is None or node.parent_id is not None:
            raise http_error(403, "FORBIDDEN")
        storage_root = node.root_id
    cursor: tuple[datetime, uuid.UUID] | None = None
    if after is not None:
        try:
            stamp, _, raw = after.rpartition("|")
            cursor = (datetime.fromisoformat(stamp), uuid.UUID(raw))
        except ValueError as exc:
            raise http_error(422, "INVALID_CURSOR") from exc
    items, more = trash.visible_items(db, actor_id, root_id=storage_root, limit=limit, after=cursor)
    out = [_item_out(db, actor_id, i) for i in items]
    next_after = f"{items[-1].trashed_at.isoformat()}|{items[-1].id}" if more and items else None
    return TrashListOut(
        items=out,
        next_after=next_after,
        roots=_manageable_roots(db, actor_id, storage_root),
        retention_days=trash.get_retention(db),
    )


def _manageable_roots(
    db: Session, actor_id: uuid.UUID, only: uuid.UUID | None
) -> list[TrashRootOut]:
    roots: list[TrashRootOut] = []
    query = select(StorageRoot)
    if only is not None:
        query = query.where(StorageRoot.id == only)
    for root in db.scalars(query):
        node = root_node(db, root.id)
        if not trash.can_manage_root(db, actor_id, node):
            continue
        items = list(
            db.scalars(
                select(TrashItem).where(
                    TrashItem.root_id == root.id, TrashItem.status == "in_trash"
                )
            )
        )
        flagged = (
            db.scalar(
                select(func.count())
                .select_from(TrashItem)
                .where(TrashItem.root_id == root.id, TrashItem.status == "reconcile_needed")
            )
            or 0
        ) + len(trash.orphan_operations(db, root.id))
        roots.append(
            TrashRootOut(
                flagged_count=int(flagged),
                root_node_id=node.id,
                kind=root.kind,
                item_count=len(items),
                total_size=sum(i.size_bytes for i in items),
                empty_token=trash.empty_token(root.id, items),
            )
        )
    return roots


@router.post("/trash/{item_id}/restore", response_model=ResultOut)
def restore_item(
    body: RestoreBody,
    request: Request,
    item: TrashItem = Depends(trash_item_access),
    principal: Principal = Depends(actor_access),
    db: Session = Depends(get_db),
    idempotency_key: str | None = IdempotencyKey,
) -> ResultOut:
    """Restores to the original folder, or to `target_parent_id` (a folder of the same root).
    A taken name is never overwritten: `keep_both` adds a number, `fail` refuses."""
    try:
        out = trash.restore(
            _ctx(request, principal, db),
            item.id,
            target_parent_id=body.target_parent_id,
            on_conflict=body.on_conflict,
            key=idempotency_key,
        )
    except OpError as err:
        raise _http(err) from err
    return _result(out)


@router.post("/trash/{item_id}/check", response_model=CheckOut)
def check_item(
    request: Request,
    item: TrashItem = Depends(trash_item_access),
    principal: Principal = Depends(actor_access),
    db: Session = Depends(get_db),
) -> CheckOut:
    """ "Check and release" for an item flagged `reconcile_needed`: looks again at what is in the
    trash and puts the item back to normal if it still matches. Changes no files."""
    try:
        result = trash.check_release(_ctx(request, principal, db), item.id)
    except OpError as err:
        raise _http(err) from err
    return CheckOut(
        released=result.released,
        reason=result.reason,  # type: ignore[arg-type]
        item=_item_out(db, principal.user.id, result.item),
    )


@router.delete("/trash/{item_id}", response_model=TrashItemOut)
def delete_forever(
    request: Request,
    confirm: str = Query(min_length=1, max_length=64),
    item: TrashItem = Depends(trash_item_access),
    principal: Principal = Depends(actor_access),
    db: Session = Depends(get_db),
) -> TrashItemOut:
    """Permanent delete of one item. `confirm` is the item's `confirm_token`, which changes if its
    size or entry count do."""
    try:
        done = trash.purge(_ctx(request, principal, db), item.id, confirm=confirm)
    except OpError as err:
        raise _http(err) from err
    return _item_out(db, principal.user.id, done)


@router.post("/roots/{node_id}/trash/check", response_model=CheckRootOut)
def check_root(
    request: Request,
    ctx: NodeContext = Depends(grant_admin_access),
    db: Session = Depends(get_db),
) -> CheckRootOut:
    """ "Check and release" for every interrupted item of one root, for its manager."""
    if ctx.node.parent_id is not None:
        raise http_error(422, "NOT_A_ROOT")
    try:
        result = trash.check_release_root(_ctx(request, ctx.principal, db), ctx.node)
    except OpError as err:
        raise _http(err) from err
    return CheckRootOut(**result)


@router.post("/roots/{node_id}/trash/empty", response_model=EmptyOut)
def empty_trash(
    body: EmptyBody,
    request: Request,
    ctx: NodeContext = Depends(grant_admin_access),
    db: Session = Depends(get_db),
) -> EmptyOut:
    """Deletes forever everything in one root's trash. For the owner of a personal folder or the
    administrator of the shared tree; `confirm` is the `empty_token` from the trash list."""
    if ctx.node.parent_id is not None:
        raise http_error(422, "NOT_A_ROOT")
    try:
        result = trash.empty_root_trash(
            _ctx(request, ctx.principal, db), ctx.node, confirm=body.confirm
        )
    except OpError as err:
        raise _http(err) from err
    return EmptyOut(**result)


# ------------------------------------------------------------------ activity


@router.get("/nodes/{node_id}/activity", response_model=ActivityOut)
def node_activity(
    ctx: NodeContext = Depends(read_access),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    before: int | None = Query(default=None, ge=1),
) -> ActivityOut:
    """History of a file, or of a folder and everything in it, newest first."""
    rows, next_before = activity.node_activity(
        db, ctx.principal.user.id, ctx.node, limit=limit, before=before
    )
    return ActivityOut(
        items=[EventModel(**vars(r)) for r in rows],
        next_before=next_before,
    )


@router.get("/activity/mine", response_model=list[RecentOut])
def my_recent_actions(
    principal: Principal = Depends(actor_access),
    db: Session = Depends(get_db),
    limit: int = Query(default=30, ge=1, le=100),
) -> list[RecentOut]:
    """The caller's own recent actions, newest first, with whether each can still be undone."""
    actor = Actor(principal.user.id)
    out: list[RecentOut] = []
    ops = db.scalars(
        select(FileOperation)
        .where(
            FileOperation.actor_user_id == principal.user.id, FileOperation.status == "committed"
        )
        .order_by(FileOperation.updated_at.desc(), FileOperation.id)
        .limit(limit)
    )
    for op in ops:
        detail = op.detail if isinstance(op.detail, dict) else {}
        node = db.get(Node, op.node_id) if op.node_id else None
        seen = (
            node is not None
            and node.status not in ("dismissed", "trashed", "purged")
            and Privilege.TRAVERSE in engine.effective(db, actor, node)
        )
        out.append(
            RecentOut(
                id=op.id,
                kind="operation",
                action=op.action,
                name=detail.get("dst_name") or detail.get("name") or detail.get("src_name"),
                at=op.updated_at,
                node_id=op.node_id if seen else None,
                reversible=undo.reversible(op),
                undone="undone_by" in detail,
            )
        )
    batches = db.scalars(
        select(ImportBatch)
        .where(
            ImportBatch.actor_user_id == principal.user.id,
            ImportBatch.status.in_(("done", "stopped", "failed")),
        )
        .order_by(ImportBatch.updated_at.desc())
        .limit(10)
    )
    for batch in batches:
        plan = batch.plan if isinstance(batch.plan, dict) else {}
        out.append(
            RecentOut(
                id=batch.id,
                kind="import",
                action="import",
                name=None,
                at=batch.finished_at or batch.updated_at,
                node_id=None,
                reversible="undone" not in plan,
                undone="undone" in plan,
            )
        )
    out.sort(key=lambda r: r.at, reverse=True)
    return out[:limit]


# ------------------------------------------------------------------ undo


@router.post("/operations/{operation_id}/undo", response_model=UndoOut)
def undo_operation(
    request: Request,
    op: FileOperation = Depends(operation_access),
    principal: Principal = Depends(actor_access),
    db: Session = Depends(get_db),
    idempotency_key: str | None = IdempotencyKey,
) -> UndoOut:
    """Reverses one of the caller's own operations if nothing relevant changed since. `409
    UNDO_CONFLICT` carries a `reason` (changed, moved, name_taken, missing, no_access) and nothing
    on disk is touched."""
    try:
        result = undo.undo_operation(_ctx(request, principal, db), op.id, idempotency_key)
    except undo.UndoConflict as conflict:
        raise _undo_conflict(conflict.reason) from conflict
    except OpError as err:
        raise _http(err) from err
    return undo_out(result)


def undo_out(result: undo.UndoResult) -> UndoOut:
    return UndoOut(
        operation=_operation(result.op),
        node=_child(result.node).model_dump(mode="json") if result.node is not None else None,
        trashed=result.trashed,
        left_in_place=[
            LeftOut(node_id=x.node_id, name=x.name, reason=x.reason) for x in result.left_in_place
        ],
        replayed=result.replay,
    )
