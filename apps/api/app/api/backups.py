"""Backup administration (ADR-015) and restore.

Administrators configure the feature and see its state, but only counts, sizes and status codes:
never file names, paths or content. Restore points and restores are addressed by the root's node
id and use the grant-administration guard, so the owner of a personal folder restores it and an
administrator restores only the shared tree: the web administrator role gives no way into a
personal root. The recovery key is accepted in a request body, used in memory and never stored or
logged.
"""

import base64
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.privacy import _verify_secret
from app.auth.deps import Principal, client_ip, get_db, http_error, require_admin
from app.auth.service import audit
from app.backup import crypto, retention
from app.backup import restore as restore_mod
from app.backup.service import BackupService
from app.backup.store import (
    FULL_EVERY_RANGE,
    INTERVAL_RANGE,
    MIN_CHAINS_RANGE,
    BackupConfig,
    KeyChanged,
    RootKeyError,
    active_root_key,
    begin_root_key,
    confirm_root_key,
    create_key_if_absent,
    load_config,
    load_key,
    pending_root_key,
    root_key_fingerprints,
    save_config,
    set_escrow,
)
from app.backup.store import rotate_key as rotate_stored_key
from app.backup.volume import backups_root, folder_usage, inspect_volume
from app.config import Settings
from app.db.models import AuthAudit, BackupRootKey, BackupRun, Node, StorageRoot
from app.domain.nodes import root_node
from app.policy import privacy
from app.policy.deps import NodeContext, grant_admin_access
from app.storage.service import ReconcilerService

router = APIRouter(prefix="/api/v1", tags=["backups"])

_restore_lock = threading.Lock()
RESTORE_STATUS = {
    "BACKUP_NOT_FOUND": 404,
    "BACKUP_ARCHIVE_MISSING": 409,
    "BACKUP_ARCHIVE_DAMAGED": 409,
    "BACKUP_CHAIN_INCOMPLETE": 409,
    "ROOT_NOT_ACTIVE": 409,
    "LOCKED": 403,
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
    # Per-file counts are shown for the shared tree only. For a personal root they are None: the
    # administrator sees status, error code and size, and the owner sees counts in restore points.
    files_added: int | None
    files_changed: int | None
    files_deleted: int | None
    files_missing: int | None
    files_unstable: int | None
    encrypted: bool


class RootOut(BaseModel):
    root_id: uuid.UUID
    kind: str
    user_id: uuid.UUID | None  # an account id only, never a name or content
    # shared: "instance" (the administrator's key). personal: none | pending | active (the
    # owner's own key; a root without an active key is not backed up while encryption is on).
    key_state: str
    escrow: bool  # the owner chose to let the administrator open this root's archives
    legacy_chains: int  # chains written under the old instance-wide key
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
    recovery_key_fingerprint: str | None = None  # of that key, to match it against the page


class RestorePointOut(BaseModel):
    id: uuid.UUID
    kind: str
    finished_at: datetime | None
    size_bytes: int | None
    encrypted: bool
    chain_id: str
    seq: int
    files_added: int
    files_changed: int
    files_deleted: int
    files_missing: int
    # plain | owner (the root's own key) | shared (the administrator's key) | legacy_instance
    key_scope: str
    key_fingerprint: str | None


class RotateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Compare-and-swap: refused with 409 KEY_CHANGED unless the stored key is still this one.
    expected_fingerprint: str | None = Field(default=None, max_length=64)


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


def _run_out(r: BackupRun, *, counts: bool = True) -> RunOut:
    return RunOut(
        id=r.id,
        kind=r.kind,
        status=r.status,
        finished_at=r.finished_at,
        size_bytes=r.size_bytes,
        error_code=r.error_code,
        files_added=r.files_added if counts else None,
        files_changed=r.files_changed if counts else None,
        files_deleted=r.files_deleted if counts else None,
        files_missing=r.files_missing if counts else None,
        files_unstable=r.files_unstable if counts else None,
        encrypted=r.encrypted,
    )


def legacy_chain_count(db: Session, root: StorageRoot) -> int:
    """Chains of a personal root written under the old instance-wide key (ADR-016 section 6)."""
    if root.kind != "personal":
        return 0
    own = root_key_fingerprints(db, root.id)
    rows = db.execute(
        select(BackupRun.chain_id, BackupRun.key_fingerprint).where(
            BackupRun.root_id == root.id,
            BackupRun.status == "ok",
            BackupRun.pruned_at.is_(None),
            BackupRun.encrypted.is_(True),
        )
    )
    return len({chain for chain, fp in rows if fp is not None and fp not in own})


def key_scope(root: StorageRoot, run: BackupRun, own: set[str]) -> str:
    if not run.encrypted:
        return "plain"
    if root.kind == "shared":
        return "shared"
    return "owner" if run.key_fingerprint in own else "legacy_instance"


def build_status(
    db: Session,
    settings: Settings,
    *,
    recovery_key: str | None = None,
    recovery_fingerprint: str | None = None,
) -> StatusOut:
    cfg, key = load_config(db), load_key(db)
    vol = inspect_volume(settings)
    used = 0
    if vol.usable:
        used = folder_usage(backups_root(settings))
    roots = list(db.scalars(select(StorageRoot).order_by(StorageRoot.created_at)))
    chains = retention.list_chains(db)
    active_keys = {
        k.root_id: k
        for k in db.scalars(select(BackupRootKey).where(BackupRootKey.state == "active"))
    }
    pending_keys = set(
        db.scalars(select(BackupRootKey.root_id).where(BackupRootKey.state == "pending"))
    )

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
        if root.kind == "shared":
            key_state = "instance"
        elif root.id in active_keys:
            key_state = "active"
        else:
            key_state = "pending" if root.id in pending_keys else "none"
        root_out.append(
            RootOut(
                root_id=root.id,
                kind=root.kind,
                key_state=key_state,
                escrow=root.id in active_keys and active_keys[root.id].escrow_blob is not None,
                legacy_chains=legacy_chain_count(db, root),
                user_id=root.owner_user_id,
                status="scan_suspended"
                if root.status == "active" and root.scan_state
                else root.status,
                chains=sum(1 for c in chains if c.root_id == root.id),
                last_run=_run_out(last, counts=root.kind == "shared") if last else None,
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
    if (
        cfg.enabled
        and cfg.encrypt
        and any(r.key_state in ("none", "pending") for r in root_out if r.kind == "personal")
    ):
        warnings.append("owner_keys_pending")  # these roots wait for their owner's key
    latest = [r.last_run for r in root_out if r.last_run]
    if any(r.status == "failed" for r in latest):
        warnings.append("last_run_failed")
    if any(r.status == "refused" and r.error_code == "BACKUP_VOLUME_TOO_SMALL" for r in latest):
        warnings.append("volume_too_small")
    if any(
        r.status == "refused"
        and r.error_code in ("BACKUP_SOURCE_UNREADABLE", "BACKUP_SOURCE_CHANGED")
        for r in latest
    ):
        warnings.append("source_unreadable")
    if vol.reason == "nested":
        warnings.append("volume_nested")
    last_run_audit = db.scalar(
        select(func.max(AuthAudit.id)).where(AuthAudit.action == "backup.run")
    )
    last_prune_failed = db.scalar(
        select(func.max(AuthAudit.id)).where(AuthAudit.action == "backup.prune_failed")
    )
    if last_prune_failed is not None and (
        last_run_audit is None or last_prune_failed > last_run_audit
    ):
        warnings.append("prune_failed")  # the run itself succeeded; old history was not removed
    if any(r.status != "active" for r in roots):
        warnings.append("frozen_roots_kept")  # their backups are kept and never pruned
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
        recovery_key_fingerprint=recovery_fingerprint,
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
    fingerprint: str | None = None
    if cfg.enabled and cfg.encrypt:
        created = create_key_if_absent(db)  # locked: only one request makes the one-time key
        if created is not None:
            recovery, fingerprint = created  # shown once, in this response only
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
    return build_status(db, settings, recovery_key=recovery, recovery_fingerprint=fingerprint)


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
    body: RotateBody | None = None,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> StatusOut:
    """New key pair for future archives (use after losing the recovery key, or when it may have
    leaked). Existing archives stay readable only with the key they were written for; each root
    starts a new chain with a full backup on the next run. Rotate again after giving the key to
    an owner for a restore: it opens every archive on the volume."""
    before = load_key(db)
    try:
        recovery, fingerprint = rotate_stored_key(db, body.expected_fingerprint if body else None)
    except KeyChanged as exc:
        db.rollback()
        raise http_error(409, "KEY_CHANGED") from exc
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
    return build_status(
        db, settings_of(request), recovery_key=recovery, recovery_fingerprint=fingerprint
    )


# ------------------------------------------------------------------ restore


def _root_of(db: Session, node: Node) -> StorageRoot:
    if node.parent_id is not None:
        raise http_error(422, "NOT_A_ROOT")
    root = db.get(StorageRoot, node.root_id)
    if root is None:
        raise http_error(404, "NOT_FOUND")
    return root


# ------------------------------------------------------------------ owner keys (ADR-016)


class OwnerKeyOut(BaseModel):
    kind: str  # personal | shared
    backups_enabled: bool
    encrypted: bool
    state: str  # none | pending | active (a personal root); instance (the shared tree)
    fingerprint: str | None  # of the active key
    pending_fingerprint: str | None
    created_at: datetime | None
    confirmed_at: datetime | None
    escrow: bool
    escrow_changed_at: datetime | None
    # False when the sealed copy was made for an administrator key that has since been rotated.
    escrow_current: bool | None
    legacy_chains: int
    recovery_key: str | None = None  # present only in the response that created it
    recovery_key_fingerprint: str | None = None


class FreshBody(BaseModel):
    """Fresh proof of identity: the six-digit PIN once the person has one, else the password."""

    model_config = ConfigDict(extra="forbid")
    pin: str | None = Field(default=None, max_length=32)
    password: str | None = Field(default=None, min_length=1, max_length=1024)


class NewKeyBody(FreshBody):
    # Compare-and-swap: the active key's fingerprint the page showed (None when it had none).
    expected_fingerprint: str | None = Field(default=None, max_length=64)


class ConfirmKeyBody(FreshBody):
    recovery_key: str = Field(max_length=200, repr=False)
    # Administrator escrow is never implied: it needs this explicit, separate consent.
    escrow_consent: bool = False


class EscrowBody(FreshBody):
    enabled: bool
    recovery_key: str | None = Field(default=None, max_length=200, repr=False)


class EscrowExportOut(BaseModel):
    root_id: uuid.UUID
    user_id: uuid.UUID | None
    key_fingerprint: str  # of the owner's key inside the blob
    sealed_to_fingerprint: str | None  # the administrator key the blob was sealed to
    sealed: str  # base64 of the sealed key: useless without the administrator recovery key


def _personal_root(db: Session, node: Node) -> StorageRoot:
    root = _root_of(db, node)
    if root.kind != "personal":
        raise http_error(422, "NOT_PERSONAL")  # the shared tree uses the administrator's key
    return root


def _owner_key_out(
    db: Session,
    root: StorageRoot,
    *,
    recovery: str | None = None,
    recovery_fp: str | None = None,
) -> OwnerKeyOut:
    cfg, admin = load_config(db), load_key(db)
    active, pending = active_root_key(db, root.id), pending_root_key(db, root.id)
    has_escrow = active is not None and active.escrow_blob is not None
    return OwnerKeyOut(
        kind=root.kind,
        backups_enabled=cfg.enabled,
        encrypted=cfg.encrypt,
        state="active" if active else "pending" if pending else "none",
        fingerprint=active.fingerprint if active else None,
        pending_fingerprint=pending.fingerprint if pending else None,
        created_at=active.created_at if active else None,
        confirmed_at=active.confirmed_at if active else None,
        escrow=has_escrow,
        escrow_changed_at=active.escrow_changed_at if active else None,
        escrow_current=(
            None
            if not has_escrow or active is None
            else admin is not None and active.escrow_key_fingerprint == admin.fingerprint
        ),
        legacy_chains=legacy_chain_count(db, root),
        recovery_key=recovery,
        recovery_key_fingerprint=recovery_fp,
    )


def _fresh(request: Request, db: Session, ctx: NodeContext, body: FreshBody) -> None:
    _verify_secret(request, db, ctx.principal, body.pin, body.password)


@router.get("/nodes/{node_id}/backup-key", response_model=OwnerKeyOut)
def owner_key_status(
    ctx: NodeContext = Depends(grant_admin_access), db: Session = Depends(get_db)
) -> OwnerKeyOut:
    root = _root_of(db, ctx.node)
    out = _owner_key_out(db, root)
    if root.kind == "shared":
        out.state = "instance"
    return out


@router.post("/nodes/{node_id}/backup-key", response_model=OwnerKeyOut)
def create_owner_key(
    body: NewKeyBody,
    request: Request,
    ctx: NodeContext = Depends(grant_admin_access),
    db: Session = Depends(get_db),
) -> OwnerKeyOut:
    """Makes a new key pair for this personal root and shows the recovery key once. The key does
    nothing until the owner proves it is saved (confirm); an earlier unconfirmed key is replaced.
    Doing this when a key is active is a rotation: the old key keeps opening its old chains."""
    root = _personal_root(db, ctx.node)
    cfg = load_config(db)
    if not cfg.enabled or not cfg.encrypt:
        raise http_error(409, "BACKUP_NOT_ENCRYPTED")
    _fresh(request, db, ctx, body)
    try:
        recovery, fp = begin_root_key(db, root.id, body.expected_fingerprint)
    except KeyChanged as exc:
        db.rollback()
        raise http_error(409, "KEY_CHANGED") from exc
    audit(
        db,
        "backup.owner_key_created",
        actor=ctx.principal.user.id,
        ip=client_ip(request),
        root_id=str(root.id),
        rotation=body.expected_fingerprint is not None,
    )
    db.commit()
    return _owner_key_out(db, root, recovery=recovery, recovery_fp=fp)


@router.post("/nodes/{node_id}/backup-key/confirm", response_model=OwnerKeyOut)
def confirm_owner_key(
    body: ConfirmKeyBody,
    request: Request,
    ctx: NodeContext = Depends(grant_admin_access),
    db: Session = Depends(get_db),
) -> OwnerKeyOut:
    """The owner presents the key back, which proves it was saved. Only then is it used for new
    archives. `escrow_consent` also seals a copy to the administrator key (off unless sent)."""
    root = _personal_root(db, ctx.node)
    _fresh(request, db, ctx, body)
    try:
        row = confirm_root_key(
            db, root.id, body.recovery_key, escrow=body.escrow_consent, admin=load_key(db)
        )
    except crypto.BadKey as exc:
        db.rollback()
        raise http_error(422, "BACKUP_KEY_INVALID") from exc
    except RootKeyError as exc:
        db.rollback()
        raise http_error(409, exc.code) from exc
    audit(
        db,
        "backup.owner_key_confirmed",
        actor=ctx.principal.user.id,
        ip=client_ip(request),
        root_id=str(root.id),
        escrow=row.escrow_blob is not None,
    )
    db.commit()
    service_of(request).request_run()
    return _owner_key_out(db, root)


@router.put("/nodes/{node_id}/backup-key/escrow", response_model=OwnerKeyOut)
def change_escrow(
    body: EscrowBody,
    request: Request,
    ctx: NodeContext = Depends(grant_admin_access),
    db: Session = Depends(get_db),
) -> OwnerKeyOut:
    """Owner-only. Enabling needs the key (it is sealed to the administrator key); disabling
    deletes the sealed copy but cannot recall anything the administrator already took."""
    root = _personal_root(db, ctx.node)
    _fresh(request, db, ctx, body)
    try:
        set_escrow(
            db, root.id, enabled=body.enabled, recovery_key=body.recovery_key, admin=load_key(db)
        )
    except crypto.BadKey as exc:
        db.rollback()
        raise http_error(422, "BACKUP_KEY_INVALID") from exc
    except RootKeyError as exc:
        db.rollback()
        raise http_error(422 if exc.code == "BACKUP_KEY_MISMATCH" else 409, exc.code) from exc
    audit(
        db,
        "backup.escrow_enabled" if body.enabled else "backup.escrow_disabled",
        actor=ctx.principal.user.id,
        ip=client_ip(request),
        root_id=str(root.id),
    )
    db.commit()
    return _owner_key_out(db, root)


@router.post("/admin/backups/roots/{root_id}/escrow", response_model=EscrowExportOut)
def export_escrow(
    root_id: uuid.UUID,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> EscrowExportOut:
    """Hands the administrator the sealed copy of an owner's key (only if that owner opted in). It
    opens only with the administrator recovery key, offline (`cli escrow-open`). This is not a way
    into the folder in the app: restore stays with the owner, and the export is audited."""
    root = db.get(StorageRoot, root_id)
    row = active_root_key(db, root_id) if root is not None and root.kind == "personal" else None
    if root is None or row is None or row.escrow_blob is None:
        raise http_error(404, "NOT_FOUND")
    audit(
        db,
        "backup.escrow_exported",
        actor=admin.user.id,
        target=root.owner_user_id,
        ip=client_ip(request),
        root_id=str(root.id),
    )
    db.commit()
    return EscrowExportOut(
        root_id=root.id,
        user_id=root.owner_user_id,
        key_fingerprint=row.fingerprint,
        sealed_to_fingerprint=row.escrow_key_fingerprint,
        sealed=base64.b64encode(row.escrow_blob).decode(),
    )


@router.get("/nodes/{node_id}/backups", response_model=list[RestorePointOut])
def list_restore_points(
    ctx: NodeContext = Depends(grant_admin_access), db: Session = Depends(get_db)
) -> list[RestorePointOut]:
    """Restore points of a root: its owner's (personal) or the administrator's (shared tree)."""
    root = _root_of(db, ctx.node)
    own = root_key_fingerprints(db, root.id)
    return [
        RestorePointOut(
            key_scope=key_scope(root, r, own),
            key_fingerprint=r.key_fingerprint,
            id=r.id,
            kind=r.kind,
            finished_at=r.finished_at,
            size_bytes=r.size_bytes,
            encrypted=r.encrypted,
            chain_id=r.chain_id,
            seq=r.seq,
            files_added=r.files_added,
            files_changed=r.files_changed,
            files_deleted=r.files_deleted,
            files_missing=r.files_missing,
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
                db,
                settings,
                root=root,
                run_id=body.run_id,
                recovery_key=body.recovery_key,
                unlocked=not privacy.locked_scopes_in(
                    db, actor, ctx.principal.session.id, root_node(db, root.id)
                ),
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
