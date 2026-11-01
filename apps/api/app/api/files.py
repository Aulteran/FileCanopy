"""File routes: browse, create folder, upload, download, rename, move, copy (ADR-013).

Ids only; no endpoint accepts a path. Every route depends on a policy guard, and the service
re-checks every additional node (targets, parents) through the same engine. Mutations accept an
optional `Idempotency-Key` header; rename and move require the node's current `revision`.
"""

import logging
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, client_ip, get_db, http_error
from app.db.models import HIDDEN_NODE_STATUSES, FileOperation, Node
from app.domain import events, fileops
from app.domain.fileops import OpCtx, OpError, Outcome
from app.policy.deps import (
    NodeContext,
    OperationAccess,
    list_access,
    read_access,
    write_access,
)
from app.storage import files

router = APIRouter(prefix="/api/v1", tags=["files"])
log = logging.getLogger("filecanopy.files")

ConflictMode = Literal["reject", "keep_both"]
IdempotencyKey = Header(default=None, alias="Idempotency-Key")
operation_access = OperationAccess()


class ChildOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    size: int | None
    revision: int
    status: str
    updated_at: datetime
    # Set for greyed-out entries (symlink, special, mounted_drive, invalid_name, undecodable_name).
    unsupported_reason: str | None = None
    # Another sibling has the same name apart from case or Unicode form (ADR-011 §6).
    name_collision: bool = False


class ChildrenOut(BaseModel):
    items: list[ChildOut]
    next_after: str | None


class OperationOut(BaseModel):
    id: uuid.UUID
    action: str
    status: str
    detail: dict[str, object] | None
    updated_at: datetime


class ResultOut(BaseModel):
    node: ChildOut
    operation: OperationOut | None
    replayed: bool = False


class FolderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)


class RenameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    expected_revision: int = Field(ge=1)


class MoveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_parent_id: uuid.UUID
    name: str | None = Field(default=None, min_length=1, max_length=255)
    expected_revision: int = Field(ge=1)
    on_conflict: ConflictMode = "reject"


class CopyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_parent_id: uuid.UUID
    name: str | None = Field(default=None, min_length=1, max_length=255)
    expected_revision: int | None = Field(default=None, ge=1)
    on_conflict: ConflictMode = "reject"


def _child(node: Node, collisions: frozenset[str] = frozenset()) -> ChildOut:
    return ChildOut(
        unsupported_reason=node.unsupported_reason,
        name_collision=node.name_key in collisions,
        id=node.id,
        name=node.name,
        kind=node.kind,
        size=node.size,
        revision=node.revision,
        status=node.status,
        updated_at=node.updated_at,
    )


def _public_detail(detail: object) -> dict[str, object] | None:
    """Journal detail without internal inode numbers or staging names."""
    if not isinstance(detail, dict):
        return None
    hidden = {"src_ino", "src_dev", "tmp"}
    return {k: v for k, v in detail.items() if k not in hidden}


def _operation(op: FileOperation | None) -> OperationOut | None:
    if op is None:
        return None
    return OperationOut(
        id=op.id,
        action=op.action,
        status=op.status,
        detail=_public_detail(op.detail),
        updated_at=op.updated_at,
    )


def _result(outcome: Outcome) -> ResultOut:
    return ResultOut(
        node=_child(outcome.node), operation=_operation(outcome.op), replayed=outcome.replay
    )


def _ctx(request: Request, principal: Principal, db: Session) -> OpCtx:
    return OpCtx(db, request.app.state.auth.settings, principal.user.id, client_ip(request))


def _http(err: OpError) -> HTTPException:
    return http_error(err.status, err.code)


@router.get("/nodes/{node_id}/children", response_model=ChildrenOut)
def list_children(
    request: Request,
    ctx: NodeContext = Depends(list_access),
    db: Session = Depends(get_db),
    limit: int = Query(default=200, ge=1, le=500),
    after: str | None = Query(default=None, max_length=255),
) -> ChildrenOut:
    try:
        rows, next_after = fileops.list_children(
            _ctx(request, ctx.principal, db), ctx.node.id, limit=limit, after=after
        )
    except OpError as err:
        raise _http(err) from err
    keys = {n.name_key for n in rows}
    colliding = frozenset(
        db.scalars(
            select(Node.name_key)
            .where(
                Node.parent_id == ctx.node.id,
                Node.name_key.in_(keys),
                Node.status.not_in(HIDDEN_NODE_STATUSES),
            )
            .group_by(Node.name_key)
            .having(func.count() > 1)
        )
    )
    return ChildrenOut(items=[_child(n, colliding) for n in rows], next_after=next_after)


@router.post("/nodes/{node_id}/folders", response_model=ResultOut, status_code=201)
def create_folder(
    body: FolderBody,
    request: Request,
    ctx: NodeContext = Depends(write_access),
    db: Session = Depends(get_db),
    idempotency_key: str | None = IdempotencyKey,
) -> ResultOut:
    try:
        out = fileops.create_folder(
            _ctx(request, ctx.principal, db), ctx.node.id, body.name, idempotency_key
        )
    except OpError as err:
        raise _http(err) from err
    return _result(out)


@router.post("/nodes/{node_id}/files", response_model=ResultOut, status_code=201)
async def upload_file(
    request: Request,
    name: str = Query(min_length=1, max_length=255),
    on_conflict: ConflictMode = "reject",
    ctx: NodeContext = Depends(write_access),
    db: Session = Depends(get_db),
    idempotency_key: str | None = IdempotencyKey,
    content_length: int | None = Header(default=None),
) -> ResultOut:
    """Raw request body is the file (streamed to a temporary file, then published atomically)."""
    op_ctx = _ctx(request, ctx.principal, db)
    if content_length is not None and content_length > op_ctx.settings.max_upload_bytes:
        raise http_error(413, "UPLOAD_TOO_LARGE")
    upload = fileops.Upload(op_ctx, ctx.node.id, name, on_conflict, idempotency_key)
    try:
        await run_in_threadpool(upload.begin)
        if upload.replayed is not None:
            return _result(upload.replayed)
        async for chunk in request.stream():
            if chunk:
                await run_in_threadpool(upload.write, chunk)
        out = await run_in_threadpool(upload.finish)
    except OpError as err:
        await run_in_threadpool(upload.abort, err.code)
        raise _http(err) from err
    except BaseException:  # client disconnect, cancellation
        await run_in_threadpool(upload.abort)
        raise
    return _result(out)


def _logged_download(
    request: Request,
    user_id: uuid.UUID,
    node_id: uuid.UUID,
    name: str,
    size: int,
    chunks: Iterator[bytes],
) -> Iterator[bytes]:
    """Records the download in the file's history once the response body starts, so a refused or
    failed request is never logged as a download (M07 plan, owner decision 2026-11-01). The
    history write never blocks the download itself."""
    try:
        with request.app.state.auth.sessions() as session:
            events.record(
                session,
                node_id,
                "downloaded",
                source="ui",
                actor_user_id=user_id,
                name=name,
                size=size,
            )
            session.commit()
    except Exception as exc:
        log.warning("download not recorded: %s", type(exc).__name__)
    yield from chunks


@router.get("/nodes/{node_id}/content")
def download(
    request: Request,
    ctx: NodeContext = Depends(read_access),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    try:
        dl = fileops.open_download(_ctx(request, ctx.principal, db), ctx.node.id)
    except OpError as err:
        raise _http(err) from err
    headers = {
        "Content-Length": str(dl.size),
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(dl.name, safe='')}",
        "ETag": f'"{dl.revision}-{dl.mtime_ns}"',
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": "sandbox",
    }
    return StreamingResponse(
        _logged_download(
            request,
            ctx.principal.user.id,
            ctx.node.id,
            dl.name,
            dl.size,
            files.read_chunks(dl.file, dl.size),
        ),
        media_type="application/octet-stream",  # never a guessed type: no inline rendering
        headers=headers,
    )


@router.post("/nodes/{node_id}/rename", response_model=ResultOut)
def rename(
    body: RenameBody,
    request: Request,
    ctx: NodeContext = Depends(write_access),
    db: Session = Depends(get_db),
    idempotency_key: str | None = IdempotencyKey,
) -> ResultOut:
    try:
        out = fileops.relocate(
            _ctx(request, ctx.principal, db),
            ctx.node.id,
            action="rename",
            expected_revision=body.expected_revision,
            target_parent_id=None,
            name=body.name,
            on_conflict="reject",
            key=idempotency_key,
        )
    except OpError as err:
        raise _http(err) from err
    return _result(out)


@router.post("/nodes/{node_id}/move", response_model=ResultOut)
def move(
    body: MoveBody,
    request: Request,
    ctx: NodeContext = Depends(write_access),
    db: Session = Depends(get_db),
    idempotency_key: str | None = IdempotencyKey,
) -> ResultOut:
    try:
        out = fileops.relocate(
            _ctx(request, ctx.principal, db),
            ctx.node.id,
            action="move",
            expected_revision=body.expected_revision,
            target_parent_id=body.target_parent_id,
            name=body.name,
            on_conflict=body.on_conflict,
            key=idempotency_key,
        )
    except OpError as err:
        raise _http(err) from err
    return _result(out)


@router.post("/nodes/{node_id}/copy", response_model=ResultOut, status_code=201)
def copy_node(
    body: CopyBody,
    request: Request,
    ctx: NodeContext = Depends(read_access),
    db: Session = Depends(get_db),
    idempotency_key: str | None = IdempotencyKey,
) -> ResultOut:
    try:
        out = fileops.copy(
            _ctx(request, ctx.principal, db),
            ctx.node.id,
            target_parent_id=body.target_parent_id,
            name=body.name,
            on_conflict=body.on_conflict,
            expected_revision=body.expected_revision,
            key=idempotency_key,
        )
    except OpError as err:
        raise _http(err) from err
    return _result(out)


@router.get("/operations/{operation_id}", response_model=OperationOut)
def get_operation(op: FileOperation = Depends(operation_access)) -> OperationOut:
    return OperationOut(
        id=op.id,
        action=op.action,
        status=op.status,
        detail=_public_detail(op.detail),
        updated_at=op.updated_at,
    )
