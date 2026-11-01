"""Setup import and folder upload routes (ADR-011 §2). Ids only: no endpoint accepts a path on the
server. Setup routes depend on the grant-administration guard (an administrator for the shared
tree, the owner for a personal folder, so the administrator role has no route into a personal
folder), folder uploads on `write`, and everything under `/imports/{id}` on the batch's own actor.
The domain layer repeats the check before every entry commit."""

import os
import uuid
from datetime import datetime
from functools import lru_cache
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, client_ip, get_db, http_error
from app.config import Settings
from app.db.models import ImportBatch, StorageRoot
from app.domain import imports, setup
from app.domain.fileops import OpCtx, OpError
from app.domain.imports import EntryResult, EntryUpload, ImportRunner, RawEntry
from app.policy.deps import (
    ImportAccess,
    NodeContext,
    grant_admin_access,
    import_list_access,
    write_access,
)

router = APIRouter(prefix="/api/v1", tags=["imports"])
import_access = ImportAccess()


class ManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    kind: Literal["dir", "file"]
    size: int | None = Field(default=None, ge=0, le=2**53)
    mtime: int | None = Field(default=None, ge=0, le=2**53)  # milliseconds since the epoch


class ManifestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[ManifestEntry]
    include_system: bool = False


class ResumeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[ManifestEntry]


@lru_cache(maxsize=16)
def _limited(model: type[BaseModel], limit: int) -> type[BaseModel]:
    """The body model with its entry list bounded by the configured `max_import_entries`."""
    return create_model(
        model.__name__,
        __base__=model,
        entries=(list[ManifestEntry], Field(max_length=limit)),
    )


def manifest_byte_cap(settings: Settings) -> int:
    """The most a manifest body may weigh: a budget per allowed entry plus slack."""
    return settings.max_import_entries * imports.MANIFEST_BYTES_PER_ENTRY + 64 * 1024


async def read_manifest[M: BaseModel](request: Request, model: type[M], settings: Settings) -> M:
    """Reads and validates a JSON manifest body. Called only after the route's guard has
    authenticated the caller (session and CSRF), so an anonymous request never gets its body read
    or parsed. The body is read in chunks and refused as soon as it passes the byte cap derived
    from `max_import_entries`."""
    cap = manifest_byte_cap(settings)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise http_error(413, "REQUEST_TOO_LARGE")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > cap:
            raise http_error(413, "REQUEST_TOO_LARGE")
    try:
        parsed = _limited(model, settings.max_import_entries).model_validate_json(bytes(buf))
    except ValidationError as err:
        if any(e["type"] == "too_long" and e["loc"] == ("entries",) for e in err.errors()):
            raise http_error(413, "TOO_MANY_ENTRIES") from err
        raise http_error(422, "VALIDATION_ERROR") from err
    return parsed  # type: ignore[return-value]


class FinishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["finish", "skip"] = "finish"


class BatchOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    target_node_id: uuid.UUID
    include_system: bool
    plan: dict[str, Any]
    counts: dict[str, int]
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    # The per-file report was deleted by the retention job (the summary above stays).
    entries_pruned: bool = False


class SetupOut(BaseModel):
    state: str
    batch: BatchOut | None = None


class EntryOut(BaseModel):
    index: int
    path: str
    kind: str
    size: int | None
    status: str
    reason: str | None
    result_name: str | None


class EntriesOut(BaseModel):
    items: list[EntryOut]
    next_after: int | None


class EntryResultOut(BaseModel):
    index: int
    status: str
    result_name: str | None
    reason: str | None = None


class ResumeOut(BaseModel):
    pending: list[dict[str, Any]]
    unmatched: int


def _runner(request: Request) -> ImportRunner:
    runner: ImportRunner = request.app.state.imports
    return runner


def _ctx(request: Request, actor: uuid.UUID, db: Session) -> OpCtx:
    return OpCtx(
        db,
        request.app.state.auth.settings,
        actor,
        client_ip(request),
        session_id=getattr(request.state, "session_id", None),
    )


def _raws(body: Any) -> list[RawEntry]:
    out: list[RawEntry] = []
    for item in body.entries:
        if item.kind == "file" and item.size is None:
            raise http_error(422, "INVALID_MANIFEST")
        out.append(RawEntry(item.path, item.kind, item.size, item.mtime))
    return out


def _view(db: Session, batch: ImportBatch) -> BatchOut:
    return BatchOut(
        id=batch.id,
        kind=batch.kind,
        status=batch.status,
        target_node_id=batch.target_node_id,
        include_system=batch.include_system,
        plan=batch.plan if isinstance(batch.plan, dict) else {},
        counts=imports.counts_of(db, batch.id),
        error_code=batch.error_code,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
        finished_at=batch.finished_at,
        entries_pruned=batch.entries_pruned_at is not None,
    )


def _http(err: OpError) -> Any:
    return http_error(err.status, err.code)


def _root_of(db: Session, ctx: NodeContext) -> StorageRoot:
    root = db.get(StorageRoot, ctx.node.root_id)
    if ctx.node.parent_id is not None or root is None:
        raise http_error(422, "NOT_A_ROOT")
    return root


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


# ---------------------------------------------------------------- setup window


@router.get("/nodes/{node_id}/setup", response_model=SetupOut)
def get_setup(
    request: Request,
    ctx: NodeContext = Depends(grant_admin_access),
    db: Session = Depends(get_db),
) -> SetupOut:
    """The root's setup state and its unfinished import, if any (for resuming)."""
    root = _root_of(db, ctx)
    imports.expire_stale(db, request.app.state.auth.settings, root.id)
    batch = db.scalars(
        select(ImportBatch)
        .where(
            ImportBatch.root_id == root.id,
            ImportBatch.actor_user_id == ctx.principal.user.id,
            ImportBatch.kind.in_(imports.SETUP_KINDS),
            ImportBatch.status.in_(imports.OPEN_STATUSES),
        )
        .order_by(ImportBatch.created_at.desc())
    ).first()
    return SetupOut(state=root.setup_state, batch=_view(db, batch) if batch else None)


@router.post("/nodes/{node_id}/setup/finish", response_model=SetupOut)
def finish_setup(
    body: FinishBody,
    request: Request,
    ctx: NodeContext = Depends(grant_admin_access),
    db: Session = Depends(get_db),
) -> SetupOut:
    """Finish or skip setup: the window closes for good. An import that is writing right now must
    be cancelled first; one that has not started is cancelled here."""
    root = _root_of(db, ctx)
    open_batches = list(
        db.scalars(
            select(ImportBatch).where(
                ImportBatch.root_id == root.id,
                ImportBatch.kind.in_(imports.SETUP_KINDS),
                ImportBatch.status.in_(imports.OPEN_STATUSES),
            )
        )
    )
    if any(b.status == "running" for b in open_batches):
        raise http_error(409, "IMPORT_IN_PROGRESS")
    op_ctx = _ctx(request, ctx.principal.user.id, db)
    for batch in open_batches:
        imports.cancel_batch(op_ctx, batch)
    setup.close_window(db, root.id, body.action, actor=ctx.principal.user.id, ip=client_ip(request))
    db.commit()
    db.refresh(root)
    return SetupOut(state=root.setup_state)


@router.post("/nodes/{node_id}/setup/import", response_model=BatchOut, status_code=201)
async def setup_import(
    request: Request,
    ctx: NodeContext = Depends(grant_admin_access),
    db: Session = Depends(get_db),
) -> BatchOut:
    """Plans a setup import from a folder manifest. Nothing is written until /start. The body is
    read only after the guard has authenticated the caller."""
    op_ctx = _ctx(request, ctx.principal.user.id, db)
    body = await read_manifest(request, ManifestBody, op_ctx.settings)
    raws = _raws(body)
    try:
        batch = await run_in_threadpool(
            imports.plan_setup_folder, op_ctx, ctx.node.id, raws, body.include_system
        )
    except OpError as err:
        raise _http(err) from err
    return _view(db, batch)


@router.post("/nodes/{node_id}/setup/import-zip", response_model=BatchOut, status_code=201)
async def setup_import_zip(
    request: Request,
    include_system: bool = False,
    ctx: NodeContext = Depends(grant_admin_access),
    db: Session = Depends(get_db),
    content_length: int | None = Header(default=None),
) -> BatchOut:
    """The raw body is a zip archive, streamed to the app's staging area (never the library) and
    listed, not extracted. Nothing is written to the library until /start."""
    op_ctx = _ctx(request, ctx.principal.user.id, db)
    settings = op_ctx.settings
    user_id = ctx.principal.user.id
    try:
        await run_in_threadpool(imports.precheck_setup, op_ctx, ctx.node.id)
        if content_length is not None and content_length > settings.max_zip_bytes:
            raise OpError(413, "ZIP_TOO_LARGE")
        # Planning is serialized per root and per person: one archive in flight at a time.
        imports.zip_slots.acquire(ctx.node.root_id, user_id)
    except OpError as err:
        raise _http(err) from err
    try:
        try:
            await run_in_threadpool(imports.check_staging_space, settings, content_length or 0)
            batch_id = uuid.uuid4()
            fd, name = await run_in_threadpool(imports.new_staging_file, settings, batch_id)
        except OpError as err:
            raise _http(err) from err
        try:
            try:
                total, checked = 0, 0
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > settings.max_zip_bytes:
                        raise OpError(413, "ZIP_TOO_LARGE")
                    if total - checked >= imports.SPACE_RECHECK_BYTES:
                        checked = total
                        await run_in_threadpool(imports.check_staging_space, settings, 0)
                    await run_in_threadpool(_write_all, fd, chunk)
            finally:
                os.close(fd)
            batch = await run_in_threadpool(
                imports.plan_setup_zip, op_ctx, ctx.node.id, batch_id, name, include_system
            )
        except OpError as err:
            imports.delete_staged(settings, name)
            raise _http(err) from err
        except BaseException:
            imports.delete_staged(settings, name)
            raise
    finally:
        imports.zip_slots.release(ctx.node.root_id, user_id)
    return _view(db, batch)


@router.post("/nodes/{node_id}/folder-uploads", response_model=BatchOut, status_code=201)
async def folder_upload(
    request: Request,
    ctx: NodeContext = Depends(write_access),
    db: Session = Depends(get_db),
) -> BatchOut:
    """Starts a folder upload into a folder the actor can write to. It merges folders and keeps
    both files on a name clash; a dropped zip is just a file and never reaches this route. The body
    is read only after the guard has authenticated the caller."""
    op_ctx = _ctx(request, ctx.principal.user.id, db)
    body = await read_manifest(request, ManifestBody, op_ctx.settings)
    raws = _raws(body)
    try:
        batch = await run_in_threadpool(
            imports.plan_folder_upload, op_ctx, ctx.node.id, raws, body.include_system
        )
    except OpError as err:
        raise _http(err) from err
    return _view(db, batch)


# ---------------------------------------------------------------- batches


@router.get("/imports", response_model=list[BatchOut])
def list_my_unfinished_imports(
    request: Request,
    principal: Principal = Depends(import_list_access),
    db: Session = Depends(get_db),
) -> list[BatchOut]:
    """The caller's own unfinished batches (planned, running or stopped), newest first, so an
    interrupted folder upload can be offered for resuming after a reload. Other people's batches
    are never listed."""
    imports.expire_stale(db, request.app.state.auth.settings)
    return [_view(db, b) for b in imports.unfinished_batches(db, principal.user.id)]


@router.get("/imports/{import_id}", response_model=BatchOut)
def get_import(
    batch: ImportBatch = Depends(import_access), db: Session = Depends(get_db)
) -> BatchOut:
    return _view(db, batch)


@router.get("/imports/{import_id}/entries", response_model=EntriesOut)
def list_import_entries(
    batch: ImportBatch = Depends(import_access),
    db: Session = Depends(get_db),
    status: Literal["pending", "writing", "done", "renamed", "skipped", "failed"] | None = None,
    after: int = Query(default=-1, ge=-1),
    limit: int = Query(default=100, ge=1, le=500),
) -> EntriesOut:
    rows = imports.list_entries(db, batch.id, status=status, after=after, limit=limit + 1)
    page = rows[:limit]
    return EntriesOut(
        items=[
            EntryOut(
                index=e.idx,
                path=e.path,
                kind=e.kind,
                size=e.size,
                status=e.status,
                reason=e.reason,
                result_name=e.result_name,
            )
            for e in page
        ],
        next_after=page[-1].idx if len(rows) > limit and page else None,
    )


@router.post("/imports/{import_id}/start", response_model=BatchOut)
def start_import(
    request: Request,
    background: BackgroundTasks,
    batch: ImportBatch = Depends(import_access),
    db: Session = Depends(get_db),
) -> BatchOut:
    """Confirms a plan. An archive is then extracted in the background; a folder import accepts
    its files at /entries/{index}."""
    ctx = _ctx(request, batch.actor_user_id, db)
    try:
        imports.start_batch(ctx, batch)
    except OpError as err:
        raise _http(err) from err
    view = _view(db, batch)
    if batch.kind == "setup_zip":
        # Started once this request's session is closed, so the extraction thread never shares
        # a connection with the request that launched it.
        background.add_task(_runner(request).start_zip, batch.id)
    return view


@router.post("/imports/{import_id}/resume", response_model=ResumeOut)
async def resume_import(
    request: Request,
    batch: ImportBatch = Depends(import_access),
    db: Session = Depends(get_db),
) -> ResumeOut:
    """Re-selecting the same folder after an interruption: returns only the files still to send.
    The body is read only after the guard has authenticated the caller."""
    ctx = _ctx(request, batch.actor_user_id, db)
    body = await read_manifest(request, ResumeBody, ctx.settings)
    raws = _raws(body)
    try:
        out = await run_in_threadpool(imports.resume_with_manifest, ctx, batch, raws)
    except OpError as err:
        raise _http(err) from err
    return ResumeOut(**out)


@router.put("/imports/{import_id}/entries/{index}", response_model=EntryResultOut)
async def put_import_entry(
    index: int,
    request: Request,
    batch: ImportBatch = Depends(import_access),
    db: Session = Depends(get_db),
    content_length: int | None = Header(default=None),
) -> EntryResultOut:
    """The raw body is one file of a folder batch. Idempotent by index: an entry that is already
    complete returns its result."""
    ctx = _ctx(request, batch.actor_user_id, db)
    upload = EntryUpload(ctx, batch.id, index, declared_length=content_length)
    result: EntryResult | None
    try:
        await run_in_threadpool(upload.begin)
        result = upload.result
        if result is None:
            async for chunk in request.stream():
                if chunk:
                    await run_in_threadpool(upload.write, chunk)
            result = await run_in_threadpool(upload.finish)
    except OpError as err:
        await run_in_threadpool(upload.abort, err.code)
        raise _http(err) from err
    except BaseException:  # the sender went away
        await run_in_threadpool(upload.abort)
        raise
    return EntryResultOut(
        index=result.index,
        status=result.status,
        result_name=result.result_name,
        reason=result.reason,
    )


@router.post("/imports/{import_id}/undo")
def undo_import(
    request: Request,
    batch: ImportBatch = Depends(import_access),
    principal: Principal = Depends(import_list_access),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Any:
    """Moves everything this batch created to the trash (nothing is deleted), except entries that
    changed since: those stay where they are and are listed in `left_in_place`."""
    from app.api.trash import undo_out
    from app.domain import undo as undo_domain

    ctx = OpCtx(db, request.app.state.auth.settings, principal.user.id, client_ip(request), session_id=principal.session.id)
    try:
        result = undo_domain.undo_import(ctx, batch, idempotency_key)
    except undo_domain.UndoConflict as conflict:
        raise HTTPException(
            status_code=409, detail={"code": "UNDO_CONFLICT", "reason": conflict.reason}
        ) from conflict
    except OpError as err:
        raise http_error(err.status, err.code) from err
    return undo_out(result)


@router.post("/imports/{import_id}/cancel", response_model=BatchOut)
def cancel_import(
    request: Request,
    batch: ImportBatch = Depends(import_access),
    db: Session = Depends(get_db),
) -> BatchOut:
    """Stops the batch. Files and folders it already created stay."""
    imports.cancel_batch(_ctx(request, batch.actor_user_id, db), batch)
    return _view(db, batch)
