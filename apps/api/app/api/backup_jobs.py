"""Backup jobs administration (M27, ADR-021). Administrators only.

A job names what it covers, whether it is encrypted, where it goes (a folder of the backup volume,
SFTP or S3-compatible storage), when it runs, and its backup type ("full" every time, or an
incremental "chain"). Responses carry only counts, status codes and the non-secret destination
settings: remote credentials are accepted once, sealed under the operator's secrets key, and never
returned, logged or audited.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.backups import service_of, settings_of
from app.auth import clock
from app.auth.deps import Principal, client_ip, get_db, http_error, require_admin
from app.auth.service import audit
from app.backup import jobs as jobs_mod
from app.backup.destinations import DestinationError, open_destination
from app.backup.jobs import JobError, spec_of
from app.backup.store import create_key_if_absent
from app.backup.volume import inspect_volume
from app.db.models import BackupJob, BackupJobChainState, BackupRun

router = APIRouter(prefix="/api/v1/admin/backups/jobs", tags=["backups"])

MAX_JOBS = 20
DEST_PUBLIC_KEYS = (
    "host",
    "port",
    "username",
    "path",
    "host_key_sha256",
    "bucket",
    "region",
    "endpoint_url",
)


class JobBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=jobs_mod.NAME_MAX)
    enabled: bool = False
    scope: str = "all"  # all | shared | personal
    backup_type: str = "chain"  # full (every time) | chain (a full run, then changed-only runs)
    chain_length: int = 7  # runs per chain, the first being the full one (chain type only)
    interval_minutes: int = 1440
    min_chains_kept: int = 2
    max_size_bytes: int | None = Field(default=None, ge=1)
    encrypt: bool = True
    allow_confidential: bool = False  # unencrypted jobs only
    destination_type: str = "local"  # local | sftp | s3
    destination: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] | None = Field(default=None, repr=False)
    # Explicit acknowledgements, each required by the situation that needs it.
    acknowledge_unencrypted: bool = False
    acknowledge_confidential: bool = False
    acknowledge_remote_unencrypted: bool = False


class JobOut(BaseModel):
    id: uuid.UUID
    name: str
    enabled: bool
    scope: str
    backup_type: str
    chain_length: int
    interval_minutes: int
    min_chains_kept: int
    max_size_bytes: int | None
    encrypt: bool
    allow_confidential: bool
    destination_type: str
    destination: dict[str, Any]
    credentials_set: bool
    warnings: list[str]
    last_status: str | None
    last_error: str | None
    last_finished_at: datetime | None
    next_due: datetime | None
    chains: int
    recovery_key: str | None = None  # only in the response that created the instance key
    recovery_key_fingerprint: str | None = None


class TestOut(BaseModel):
    ok: bool = True


def _warnings(job: BackupJob) -> list[str]:
    out: list[str] = []
    if not job.encrypt:
        out.append("unencrypted")
        if job.destination_type != "local":
            out.append("remote_unencrypted")
        out.append("confidential_included" if job.allow_confidential else "confidential_excluded")
    if job.destination_type == "sftp" and not (job.destination or {}).get("host_key_sha256"):
        out.append("host_key_unpinned")
    if job.destination_type != "local" and job.credentials_sealed is None:
        out.append("credentials_missing")
    return out


def _out(db: Session, job: BackupJob, **extra: Any) -> JobOut:
    last = db.scalars(
        select(BackupRun)
        .where(BackupRun.job_id == job.id, BackupRun.status != "running")
        .order_by(BackupRun.started_at.desc())
    ).first()
    chains = db.scalar(
        select(func.count(func.distinct(BackupRun.chain_id))).where(
            BackupRun.job_id == job.id, BackupRun.status == "ok", BackupRun.pruned_at.is_(None)
        )
    )
    next_due = None
    if job.enabled and last is not None:
        next_due = (last.finished_at or last.started_at) + timedelta(minutes=job.interval_minutes)
    return JobOut(
        id=job.id,
        name=job.name,
        enabled=job.enabled,
        scope=job.scope,
        backup_type=job.backup_type,
        chain_length=job.chain_length,
        interval_minutes=job.interval_minutes,
        min_chains_kept=job.min_chains_kept,
        max_size_bytes=job.max_size_bytes,
        encrypt=job.encrypt,
        allow_confidential=job.allow_confidential,
        destination_type=job.destination_type,
        destination={k: v for k, v in (job.destination or {}).items() if k in DEST_PUBLIC_KEYS},
        credentials_set=job.credentials_sealed is not None,
        warnings=_warnings(job),
        last_status=last.status if last else None,
        last_error=last.error_code if last else None,
        last_finished_at=last.finished_at if last else None,
        next_due=next_due,
        chains=int(chains or 0),
        **extra,
    )


def _public(job: BackupJob) -> dict[str, Any]:
    """The audit view of a job: every setting, no credentials."""
    d = {
        c: getattr(job, c)
        for c in (
            "name",
            "enabled",
            "scope",
            "backup_type",
            "chain_length",
            "interval_minutes",
            "min_chains_kept",
            "max_size_bytes",
            "encrypt",
            "allow_confidential",
            "destination_type",
        )
    }
    d["destination"] = {k: v for k, v in (job.destination or {}).items() if k in DEST_PUBLIC_KEYS}
    return d


def _get(db: Session, job_id: uuid.UUID) -> BackupJob:
    job = db.get(BackupJob, job_id)
    if job is None:
        raise http_error(404, "JOB_NOT_FOUND")
    return job


def _validate(
    request: Request, db: Session, body: JobBody, existing: BackupJob | None
) -> tuple[dict[str, Any], dict[str, str] | None]:
    """The checked column values and the (clean) new credentials, or an HTTP error."""
    settings = settings_of(request)
    try:
        name = jobs_mod.check_job_fields(
            name=body.name,
            backup_type=body.backup_type,
            scope=body.scope,
            interval_minutes=body.interval_minutes,
            chain_length=body.chain_length,
            min_chains_kept=body.min_chains_kept,
        )
        if body.destination_type not in jobs_mod.DEST_TYPES:
            raise JobError("DESTINATION_TYPE_INVALID")
        destination = jobs_mod.clean_destination(body.destination_type, body.destination)
        creds = jobs_mod.clean_credentials(body.destination_type, body.credentials)
    except JobError as exc:
        raise http_error(422, exc.code) from exc
    clash = db.scalar(select(BackupJob.id).where(func.lower(BackupJob.name) == name.lower()))
    if clash is not None and (existing is None or clash != existing.id):
        raise http_error(409, "JOB_NAME_TAKEN")
    remote = body.destination_type != "local"
    if not body.encrypt:
        if not body.acknowledge_unencrypted:
            raise http_error(422, "UNENCRYPTED_NOT_ACKNOWLEDGED")
        if remote and not body.acknowledge_remote_unencrypted:
            raise http_error(422, "REMOTE_UNENCRYPTED_NOT_ACKNOWLEDGED")
        if body.allow_confidential and not body.acknowledge_confidential:
            raise http_error(422, "CONFIDENTIAL_NOT_ACKNOWLEDGED")
    if remote:
        if not jobs_mod.box_of(settings).configured:
            raise http_error(409, "SECRETS_KEY_MISSING")  # nowhere safe to keep credentials
        same_kind = existing is not None and existing.destination_type == body.destination_type
        if creds is None and not (same_kind and existing and existing.credentials_sealed):
            raise http_error(422, "CREDENTIALS_REQUIRED")
    if body.enabled and not inspect_volume(settings).usable:
        raise http_error(409, "BACKUP_VOLUME_UNAVAILABLE")  # remote jobs stage on the volume too
    values: dict[str, Any] = {
        "name": name,
        "enabled": body.enabled,
        "scope": body.scope,
        "encrypt": body.encrypt,
        "allow_confidential": bool(body.allow_confidential and not body.encrypt),
        "backup_type": body.backup_type,
        "interval_minutes": body.interval_minutes,
        "chain_length": body.chain_length if body.backup_type == "chain" else 1,
        "min_chains_kept": body.min_chains_kept,
        "max_size_bytes": body.max_size_bytes,
        "destination_type": body.destination_type,
        "destination": destination,
    }
    return values, creds


def _apply(
    request: Request, job: BackupJob, values: dict[str, Any], creds: dict[str, str] | None
) -> None:
    for column, value in values.items():
        setattr(job, column, value)
    if job.destination_type == "local":
        job.credentials_sealed = None
    elif creds is not None:
        job.credentials_sealed = jobs_mod.seal_credentials(settings_of(request), job.id, creds)
    job.updated_at = clock.utcnow()


def _instance_key(db: Session, job: BackupJob) -> dict[str, str]:
    """An enabled encrypted job needs the instance recovery key (shared tree); made once."""
    if job.enabled and job.encrypt:
        created = create_key_if_absent(db)
        if created is not None:
            return {"recovery_key": created[0], "recovery_key_fingerprint": created[1]}
    return {}


@router.get("", response_model=list[JobOut])
def list_jobs(_: Principal = Depends(require_admin), db: Session = Depends(get_db)) -> list[JobOut]:
    rows = db.scalars(select(BackupJob).order_by(BackupJob.created_at)).all()
    return [_out(db, j) for j in rows]


@router.post("", response_model=JobOut, status_code=201)
def create_job(
    body: JobBody,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> JobOut:
    if (db.scalar(select(func.count(BackupJob.id))) or 0) >= MAX_JOBS:
        raise http_error(409, "TOO_MANY_JOBS")
    values, creds = _validate(request, db, body, None)
    now = clock.utcnow()
    job = BackupJob(id=uuid.uuid4(), created_by=admin.user.id, created_at=now, updated_at=now)
    _apply(request, job, values, creds)
    db.add(job)
    extra = _instance_key(db, job)
    db.flush()
    audit(
        db,
        "backup.job_created",
        actor=admin.user.id,
        ip=client_ip(request),
        job_id=str(job.id),
        job=_public(job),
        credentials_set=job.credentials_sealed is not None,
        key_created=bool(extra),
    )
    db.commit()
    if job.enabled:
        service_of(request).request_run(frozenset({job.id}))
    return _out(db, job, **extra)


@router.put("/{job_id}", response_model=JobOut)
def update_job(
    job_id: uuid.UUID,
    body: JobBody,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> JobOut:
    job = _get(db, job_id)
    before = _public(job)
    values, creds = _validate(request, db, body, job)
    _apply(request, job, values, creds)
    extra = _instance_key(db, job)
    db.flush()
    audit(
        db,
        "backup.job_changed",
        actor=admin.user.id,
        ip=client_ip(request),
        job_id=str(job.id),
        before=before,
        after=_public(job),
        credentials_changed=creds is not None,
        key_created=bool(extra),
    )
    db.commit()
    return _out(db, job, **extra)


@router.delete("/{job_id}")
def delete_job(
    job_id: uuid.UUID,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Only a job without stored backups can be removed: its archives and (remote) objects are
    never orphaned silently. Disable it instead, or let retention age its history out first."""
    job = _get(db, job_id)
    held = db.scalar(
        select(func.count(BackupRun.id)).where(
            BackupRun.job_id == job.id, BackupRun.status == "ok", BackupRun.pruned_at.is_(None)
        )
    )
    if held:
        raise http_error(409, "JOB_HAS_BACKUPS")
    for state in db.scalars(
        select(BackupJobChainState).where(BackupJobChainState.job_id == job.id)
    ):
        db.delete(state)
    audit(
        db,
        "backup.job_deleted",
        actor=admin.user.id,
        ip=client_ip(request),
        job_id=str(job.id),
        job=_public(job),
    )
    db.delete(job)
    db.commit()
    return {"status": "deleted"}


@router.post("/{job_id}/test", response_model=TestOut)
def test_job(
    job_id: uuid.UUID,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> TestOut:
    """Connects, writes and removes a small probe object. A SFTP server whose host key is not
    pinned yet is refused, and the key it presented is returned so the administrator can compare
    it with the one they expect and pin it."""
    job = _get(db, job_id)
    if job.destination_type == "local":
        return TestOut()
    code: str | None = None
    detail: str | None = None
    try:
        dest = open_destination(spec_of(job), settings_of(request))
        try:
            if dest is not None:
                dest.test()
        finally:
            if dest is not None:
                dest.close()
    except DestinationError as exc:
        code, detail = exc.code, exc.detail
    audit(
        db,
        "backup.job_tested",
        actor=admin.user.id,
        ip=client_ip(request),
        job_id=str(job.id),
        ok=code is None,
        code=code,
    )
    db.commit()
    if code is not None:
        body: dict[str, Any] = {"code": code}
        if detail and code.startswith("SFTP_HOST_KEY"):
            body["host_key_sha256"] = detail  # a public fingerprint, not a secret
        raise HTTPException(status_code=409, detail=body)
    return TestOut()


@router.post("/{job_id}/run", status_code=202)
def run_job(
    job_id: uuid.UUID,
    request: Request,
    admin: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    job = _get(db, job_id)
    if not job.enabled:
        raise http_error(409, "BACKUP_NOT_ENABLED")
    if not service_of(request).request_run(frozenset({job.id})):
        raise http_error(429, "BACKUP_RUN_TOO_SOON")
    audit(
        db,
        "backup.run_requested",
        actor=admin.user.id,
        ip=client_ip(request),
        job_id=str(job.id),
    )
    db.commit()
    return {"status": "queued"}
