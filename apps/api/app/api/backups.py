"""Backup administration (ADR-015) and restore.

Administrators configure the feature and see its state, but only counts, sizes and status codes:
never file names, paths or content. Restore points and restores are addressed by the root's node
id and use the grant-administration guard, so the owner of a personal folder restores it and an
administrator restores only the shared tree: the web administrator role gives no way into a
personal root. The recovery key is accepted in a request body, used in memory and never stored or
logged.
"""

import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, client_ip, get_db, http_error, require_admin
from app.auth.service import audit
from app.backup import restore as restore_mod
from app.backup import retention
from app.backup.service import BackupService
from app.backup.store import (
    FULL_EVERY_RANGE,
    INTERVAL_RANGE,
    MIN_CHAINS_RANGE,
    BackupConfig,
    load_config,
    load_key,
    new_key,
    save_config,
)
from app.backup.volume import backups_root, folder_usage, inspect_volume
from app.config import Settings
from app.db.models import BackupRun, Node, StorageRoot
from app.domain.nodes import root_node
from app.policy.deps import NodeContext, grant_admin_access
from app.storage.service import ReconcilerService

router = APIRouter(prefix="/api/v1", tags=["backups"])

_restore_lock = threading.Lock()
RESTORE_STATUS = {
    "BACKUP_NOT_FOUND": 404,
    "BACKUP_ARCHIVE_MISSING": 409,
    "BACKUP_ARCHIVE_DAMAGED": 409,
    "BACKUP_CHAIN_INCOMPLETE": 409,
    "RESTORE_NO_SPACE": 409,
    "RESTORE_FAILED": 500,
}


# ------------------------------------------------------------------ models


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    interval_minutes: int = Field(ge=INTERVAL_RANGE[0], le=INTERVAL_RANGE[1])
    full_every: int = Field(ge=FULL_EVERY_RANGE[0], le=FULL_EVERY_RANGE[1])
    min_chains_kept: int = Field(ge=MIN_CHAINS_RANGE[0], le=MIN_CHAINS_RANGE[1])
    max_size_bytes: int | None = Field(default=None, ge=1)
    encrypt: bool
    # Required to switch encryption off: archives then hold everyone's files in the clear.
    acknowledge_unencrypted: bool = False


class VolumeOut(BaseModel):
    configured: bool
    usable: bool
    reason: str | None
    same_device: bool | None
    free_bytes: int | None
    total_bytes: int | None
    used_bytes: int


class KeyOut(BaseModel):
    present: bool
    fingerprint: str | None
    created_at: str | None


class RunOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    finished_at: datetime | None
    size_bytes: int | None
    error_code: str | None
    files_added: int
    files_changed: int
    files_deleted: int
    files_missing: int
    files_unstable: int
    encrypted: bool


class RootOut(BaseModel):
    kind: str
    user_id: uuid.UUID | None  # an account id only, never a name or content
    status: str
    chains: int
    last_run: RunOut | None
    next_due: datetime | None


class EstimatesOut(BaseModel):
    last_full_bytes: int
    avg_incremental_bytes: int
    chain_bytes: int
    chains_held: int | None
    history_days: float | None


class StatusOut(BaseModel):
    settings: SettingsBody
    volume: VolumeOut
    key: KeyOut
    warnings: list[str]
    estimates: EstimatesOut
    roots: list[RootOut]
    recovery_key: str | None = None  # present only in the response that created it


class RestorePointOut(BaseModel):
    id: uuid.UUID
    kind: str
    finished_at: datetime | None
    size_bytes: int | None
    encrypted: bool
    chain_id: str
    seq: int


class RestoreBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: uuid.UUID | None = None
    recovery_key: str | None = Field(default=None, max_length=200, repr=False)


class RestoreOut(BaseModel):
    folder_node_id: uuid.UUID | None
    folder_name: str
    files: int
    bytes_written: int
    run_id: uuid.UUID


class Queued(BaseModel):
    status: str = "queued"


# ------------------------------------------------------------------ helpers


def service_of(request: Request) -> BackupService:
    service: BackupService = request.app.state.backup
    return service


def settings_of(request: Request) -> Settings:
    cfg: Settings = request.app.state.auth.settings
    return cfg


def _body_of(cfg: BackupConfig) -> SettingsBody:
    return SettingsBody(**asdict(cfg), acknowledge_unencrypted=not cfg.encrypt)


def _run_out(r: BackupRun) -> RunOut:
    return RunOut(
        id=r.id,
        kind=r.kind,
        status=r.status,
        finished_at=r.finished_at,
        size_bytes=r.size_bytes,
        error_code=r.error_code,
        files_added=r.files_added,
        files_changed=r.files_changed,
        files_deleted=r.files_deleted,
        files_missing=r.files_missing,
        files_unstable=r.files_unstable,
        encrypted=r.encrypted,
    )


def build_status(db: Session, settings: Settings, *, recovery_key: str | None = None) -> StatusOut:
    cfg, key = load_config(db), load_key(db)
    vol = inspect_volume(settings)
    used = 0
    if vol.usable:
        used = folder_usage(backups_root(settings))
    roots = list(db.scalars(select(StorageRoot).order_by(StorageRoot.created_at)))
    chains = retention.list_chains(db)

    # sizes for the estimates: newest full and mean changed-only run of each root's newest chain
    last_full = 0
    avg_inc_total = 0
    root_out: list[RootOut] = []
    for root in roots:
        runs = list(
            db.scalars(
                select(BackupRun)
                .where(BackupRun.root_id == root.id, BackupRun.pruned_at.is_(None))
                .order_by(BackupRun.started_at.desc())
            )
        )
        ok = [r for r in runs if r.status == "ok"]
        fulls = [r for r in ok if r.kind == "full"]
        if fulls:
            newest = fulls[0]
            last_full += newest.size_bytes or 0
            incs = [
                r
                for r in ok
                if r.kind == "incremental" and r.chain_id == newest.chain_id and r.size_bytes
            ]
            if incs:
                avg_inc_total += sum(r.size_bytes or 0 for r in incs) // len(incs)
        attempts = [r for r in runs if r.status != "running"]
        last = attempts[0] if attempts else None
        next_due = None
        if cfg.enabled and last is not None:
            next_due = (last.finished_at or last.started_at) + timedelta(
                minutes=cfg.interval_minutes
            )
        root_out.append(
            RootOut(
                kind=root.kind,
                user_id=root.owner_user_id,
                status="scan_suspended"
                if root.status == "active" and root.scan_state
                else root.status,
                chains=sum(1 for c in chains if c.root_id == root.id),
                last_run=_run_out(last) if last else None,
                next_due=next_due,
            )
        )
    chain_bytes = last_full + max(cfg.full_every - 1, 0) * avg_inc_total
    held: int | None = None
    days: float | None = None
    if cfg.max_size_bytes and chain_bytes > 0:
        held = cfg.max_size_bytes // chain_bytes
        days = round(held * cfg.full_every * cfg.interval_minutes / 1440, 1)

    warnings: list[str] = []
    if not vol.configured:
        warnings.append("volume_not_configured")
    elif not vol.usable:
        warnings.append("volume_unavailable")
    elif vol.same_device:
        warnings.append("same_device")
    if cfg.min_chains_kept == 1:
        warnings.append("single_chain")
    if held is not None and held < cfg.min_chains_kept:
        warnings.append("limit_too_small")
    if cfg.max_size_bytes and used > cfg.max_size_bytes:
        warnings.append("over_limit")
    if not cfg.encrypt:
        warnings.append("unencrypted")
    if cfg.enabled and cfg.encrypt and key is None:
        warnings.append("key_missing")
    latest = [r.last_run for r in root_out if r.last_run]
    if any(r.status == "failed" for r in latest):
        warnings.append("last_run_failed")
    if any(r.status == "refused" and r.error_code == "BACKUP_VOLUME_TOO_SMALL" for r in latest):
        warnings.append("volume_too_small")
    return StatusOut(
        settings=_body_of(cfg),
        volume=VolumeOut(
            configured=vol.configured,
            usable=vol.usable,
            reason=vol.reason,
            same_device=vol.same_device,
            free_bytes=vol.free_bytes,
            total_bytes=vol.total_bytes,
            used_bytes=used,
        ),
        key=KeyOut(
            present=key is not None,
            fingerprint=key.fingerprint if key else None,
            created_at=key.created_at if key else None,
        ),
        warnings=warnings,
        estimates=EstimatesOut(
            last_full_bytes=last_full,
            avg_incremental_bytes=avg_inc_total,
            chain_bytes=chain_bytes,
            chains_held=held,
            history_days=days,
        ),
        roots=root_out,
        recovery_key=recovery_key,
    )


# ------------------------------------------------------------------ administration


@router.get("/admin/backups", response_model=StatusOut)
def backup_status(
    request: Request, _: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> StatusOut:
    return build_status(db, settings_of(request))


@router.put("/admin/backups/settings", response_model=StatusOut)
def save_settings(
    body: SettingsBody,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> StatusOut:
    settings = settings_of(request)
    if not body.encrypt and not body.acknowledge_unencrypted:
        raise http_error(422, "UNENCRYPTED_NOT_ACKNOWLEDGED")
    if body.enabled and not inspect_volume(settings).usable:
        raise http_error(409, "BACKUP_VOLUME_UNAVAILABLE")
    before = load_config(db)
    cfg = BackupConfig(
        enabled=body.enabled,
        interval_minutes=body.interval_minutes,
        full_every=body.full_every,
        min_chains_kept=body.min_chains_kept,
        max_size_bytes=body.max_size_bytes,
        encrypt=body.encrypt,
    )
    recovery: str | None = None
    if cfg.enabled and cfg.encrypt and load_key(db) is None:
        recovery = new_key(db)  # shown once, in this response only
    save_config(db, cfg)
    audit(
        db,
        "backup.settings_changed",
        actor=admin.user.id,
        ip=client_ip(request),
        before=asdict(before),
        after=asdict(cfg),
        key_created=recovery is not None,
    )
    db.commit()
    if cfg.enabled:
        service_of(request).request_run()
    return build_status(db, settings, recovery_key=recovery)


@router.post("/admin/backups/run", response_model=Queued, status_code=202)
def run_now(
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Queued:
    if not load_config(db).enabled:
        raise http_error(409, "BACKUP_NOT_ENABLED")
    if not service_of(request).request_run():
        raise http_error(429, "BACKUP_RUN_TOO_SOON")
    audit(db, "backup.run_requested", actor=admin.user.id, ip=client_ip(request))
    db.commit()
    return Queued()


@router.post("/admin/backups/key/rotate", response_model=StatusOut)
def rotate_key(
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> StatusOut:
    """New key pair for future archives (use after losing the recovery key, or when it may have
    leaked). Existing archives stay readable only with the key they were written for; each root
    starts a new chain with a full backup on the next run."""
    before = load_key(db)
    recovery = new_key(db)
    audit(
        db,
        "backup.key_rotated",
        actor=admin.user.id,
        ip=client_ip(request),
        previous_fingerprint=before.fingerprint if before else None,
    )
    db.commit()
    if load_config(db).enabled:
        service_of(request).request_run()
    return build_status(db, settings_of(request), recovery_key=recovery)


# ------------------------------------------------------------------ restore


def _root_of(db: Session, node: Node) -> StorageRoot:
    if node.parent_id is not None:
        raise http_error(422, "NOT_A_ROOT")
    root = db.get(StorageRoot, node.root_id)
    if root is None:
        raise http_error(404, "NOT_FOUND")
    return root


@router.get("/nodes/{node_id}/backups", response_model=list[RestorePointOut])
def list_restore_points(
    ctx: NodeContext = Depends(grant_admin_access), db: Session = Depends(get_db)
) -> list[RestorePointOut]:
    """Restore points of a root: its owner's (personal) or the administrator's (shared tree)."""
    root = _root_of(db, ctx.node)
    return [
        RestorePointOut(
            id=r.id,
            kind=r.kind,
            finished_at=r.finished_at,
            size_bytes=r.size_bytes,
            encrypted=r.encrypted,
            chain_id=r.chain_id,
            seq=r.seq,
        )
        for r in restore_mod.restore_points(db, root.id)
    ]


@router.post("/nodes/{node_id}/backups/restore", response_model=RestoreOut)
def restore_backup(
    body: RestoreBody,
    request: Request,
    ctx: NodeContext = Depends(grant_admin_access),
    db: Session = Depends(get_db),
) -> RestoreOut:
    """Rebuilds the root as of a restore point in a new folder at its top; overwrites nothing."""
    root = _root_of(db, ctx.node)
    settings = settings_of(request)
    actor = ctx.principal.user.id
    if not _restore_lock.acquire(blocking=False):
        raise http_error(409, "RESTORE_IN_PROGRESS")
    try:
        try:
            result = restore_mod.restore(
                db, settings, root=root, run_id=body.run_id, recovery_key=body.recovery_key
            )
        except restore_mod.RestoreError as exc:
            audit(
                db,
                "backup.restore_failed",
                actor=actor,
                ip=client_ip(request),
                root_id=str(root.id),
                code=exc.code,
            )
            db.commit()
            status = RESTORE_STATUS.get(exc.code, 422)
            raise http_error(status, exc.code) from exc
        top = root_node(db, root.id)
        service: ReconcilerService = request.app.state.reconciler
        service.reconciler.run_root(root.id, "manual", subtree=top.id)  # register the new folder
        folder = db.scalar(
            select(Node).where(
                Node.parent_id == top.id,
                Node.name == result.folder_name,
                Node.status == "active",
            )
        )
        audit(
            db,
            "backup.restored",
            actor=actor,
            ip=client_ip(request),
            root_id=str(root.id),
            run_id=str(result.run_id),
            files=result.files,
            bytes=result.bytes_written,
        )
        db.commit()
        return RestoreOut(
            folder_node_id=folder.id if folder else None,
            folder_name=result.folder_name,
            files=result.files,
            bytes_written=result.bytes_written,
            run_id=result.run_id,
        )
    finally:
        _restore_lock.release()
