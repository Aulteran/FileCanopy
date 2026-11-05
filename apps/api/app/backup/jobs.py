"""Backup jobs (M27, ADR-021): the resolved settings a run needs, validation, and credentials.

The *default job* is the global backup settings (`app_setting`), unchanged since M06c; its runs
carry no `job_id` and keep their folder layout. Every other job is a `backup_job` row with its own
folder `j-<id8>/` in the backup volume, its own chain state and its own retention.

"Full every time" is a chain of length 1 (every run is a full run); "Incremental chain" is a full
run followed by `chain_length - 1` changed-only runs. Credentials for remote destinations are
sealed with the operator's secrets key and are only ever opened by `destinations.open_destination`;
they are never returned, logged or audited.
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.secrets import SecretBox, SecretsError
from app.backup.store import (
    FULL_EVERY_RANGE,
    INTERVAL_RANGE,
    MIN_CHAINS_RANGE,
    BackupConfig,
    load_config,
)
from app.config import Settings
from app.db.models import BackupJob

DEST_TYPES = ("local", "sftp", "s3")
BACKUP_TYPES = ("full", "chain")
SCOPES = ("all", "shared", "personal")
NAME_MAX = 60


class JobError(Exception):
    """A job definition that cannot be accepted. `code` is an API error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class JobSpec:
    """What the engine, scheduler and retention need to run one job. Same attribute names as
    `BackupConfig` where they overlap, so the engine treats both the same way."""

    job_id: uuid.UUID | None  # None = the default job (global settings)
    name: str
    enabled: bool
    interval_minutes: int
    full_every: int  # 1 = full every time
    min_chains_kept: int
    max_size_bytes: int | None
    encrypt: bool
    allow_confidential: bool = True  # the default job has always included everything
    scope: str = "all"
    destination_type: str = "local"
    destination: dict[str, Any] = field(default_factory=dict)
    sealed: bytes | None = field(default=None, repr=False)

    @property
    def prefix(self) -> str:
        """Folder inside the backup volume ('' for the default job: unchanged layout)."""
        return "" if self.job_id is None else f"j-{self.job_id.hex[:8]}"

    @property
    def remote(self) -> bool:
        return self.destination_type != "local"

    @property
    def excluded_labels(self) -> frozenset[str]:
        """Privacy labels an unencrypted job must not copy in the clear (unless allowed)."""
        if self.encrypt or self.allow_confidential:
            return frozenset()
        return frozenset({"confidential"})


def default_spec(cfg: BackupConfig) -> JobSpec:
    return JobSpec(
        job_id=None,
        name="Default",
        enabled=cfg.enabled,
        interval_minutes=cfg.interval_minutes,
        full_every=cfg.full_every,
        min_chains_kept=cfg.min_chains_kept,
        max_size_bytes=cfg.max_size_bytes,
        encrypt=cfg.encrypt,
    )


def spec_of(job: BackupJob) -> JobSpec:
    return JobSpec(
        job_id=job.id,
        name=job.name,
        enabled=job.enabled,
        interval_minutes=job.interval_minutes,
        full_every=1 if job.backup_type == "full" else job.chain_length,
        min_chains_kept=job.min_chains_kept,
        max_size_bytes=job.max_size_bytes,
        encrypt=job.encrypt,
        allow_confidential=job.allow_confidential,
        scope=job.scope,
        destination_type=job.destination_type,
        destination=dict(job.destination or {}),
        sealed=job.credentials_sealed,
    )


def load_spec(db: Session, job_id: uuid.UUID | None) -> JobSpec | None:
    if job_id is None:
        return default_spec(load_config(db))
    job = db.get(BackupJob, job_id)
    return spec_of(job) if job else None


def all_specs(db: Session) -> list[JobSpec]:
    rows = db.scalars(select(BackupJob).order_by(BackupJob.created_at)).all()
    return [default_spec(load_config(db)), *(spec_of(j) for j in rows)]


# ------------------------------------------------------------------ validation

_HOST_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:[]")


def _text(value: Any, maximum: int, code: str, *, required: bool = True) -> str:
    if value is None or value == "":
        if required:
            raise JobError(code)
        return ""
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise JobError(code)
    return value.strip()


def _remote_path(value: Any) -> str:
    """A path prefix on the remote side: relative parts only, no traversal."""
    text = _text(value, 200, "DESTINATION_INVALID", required=False).strip("/")
    parts = [p for p in text.split("/") if p]
    if any(p in (".", "..") or "\\" in p for p in parts):
        raise JobError("DESTINATION_INVALID")
    return "/".join(parts)


def clean_destination(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    """The non-secret destination settings, checked and reduced to the known keys."""
    if kind == "local":
        return {}
    if kind == "sftp":
        host = _text(raw.get("host"), 253, "DESTINATION_INVALID")
        if not host or not set(host) <= _HOST_CHARS:
            raise JobError("DESTINATION_INVALID")
        port = raw.get("port", 22)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise JobError("DESTINATION_INVALID")
        pin = _text(raw.get("host_key_sha256"), 80, "DESTINATION_INVALID", required=False)
        return {
            "host": host,
            "port": port,
            "username": _text(raw.get("username"), 64, "DESTINATION_INVALID"),
            "path": _remote_path(raw.get("path")),
            # OpenSSH style "SHA256:<base64>": the host key is pinned, never trusted on first use
            "host_key_sha256": pin,
        }
    if kind == "s3":
        bucket = _text(raw.get("bucket"), 63, "DESTINATION_INVALID")
        if not (3 <= len(bucket) <= 63) or not set(bucket) <= set(
            "abcdefghijklmnopqrstuvwxyz0123456789.-"
        ):
            raise JobError("DESTINATION_INVALID")
        endpoint = _text(raw.get("endpoint_url"), 200, "DESTINATION_INVALID", required=False)
        if endpoint and not endpoint.startswith("https://"):
            raise JobError("DESTINATION_INSECURE")  # S3-compatible endpoints must use TLS
        return {
            "bucket": bucket,
            "region": _text(raw.get("region"), 32, "DESTINATION_INVALID", required=False),
            "endpoint_url": endpoint,
            "path": _remote_path(raw.get("path")),
        }
    raise JobError("DESTINATION_TYPE_INVALID")


def clean_credentials(kind: str, raw: dict[str, Any] | None) -> dict[str, str] | None:
    """The secret part. None = not supplied (keep the stored one on update)."""
    if raw is None:
        return None
    if kind == "sftp":
        out = {
            k: _text(raw.get(k), 8192, "CREDENTIALS_INVALID", required=False)
            for k in ("password", "private_key", "passphrase")
        }
        if not out["password"] and not out["private_key"]:
            raise JobError("CREDENTIALS_INVALID")
        return {k: v for k, v in out.items() if v}
    if kind == "s3":
        return {
            "access_key_id": _text(raw.get("access_key_id"), 128, "CREDENTIALS_INVALID"),
            "secret_access_key": _text(raw.get("secret_access_key"), 256, "CREDENTIALS_INVALID"),
        }
    return None


def int_in(value: Any, rng: tuple[int, int], code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not rng[0] <= value <= rng[1]:
        raise JobError(code)
    return value


def check_job_fields(
    *,
    name: str,
    backup_type: str,
    scope: str,
    interval_minutes: int,
    chain_length: int,
    min_chains_kept: int,
) -> str:
    clean = _text(name, NAME_MAX, "JOB_NAME_INVALID")
    if not clean:
        raise JobError("JOB_NAME_INVALID")
    if backup_type not in BACKUP_TYPES:
        raise JobError("BACKUP_TYPE_INVALID")
    if scope not in SCOPES:
        raise JobError("SCOPE_INVALID")
    int_in(interval_minutes, INTERVAL_RANGE, "SCHEDULE_INVALID")
    int_in(chain_length, FULL_EVERY_RANGE, "SCHEDULE_INVALID")
    int_in(min_chains_kept, MIN_CHAINS_RANGE, "SCHEDULE_INVALID")
    return clean


# ------------------------------------------------------------------ credentials


def box_of(settings: Settings) -> SecretBox:
    return SecretBox.from_setting(settings.secrets_key.get_secret_value())


def credentials_aad(job_id: uuid.UUID) -> bytes:
    # bound to the job: a ciphertext copied onto another job does not open
    return f"fc-backup-job:{job_id}".encode()


def seal_credentials(settings: Settings, job_id: uuid.UUID, creds: dict[str, str]) -> bytes:
    return box_of(settings).seal(json.dumps(creds, separators=(",", ":")), credentials_aad(job_id))


def open_credentials(settings: Settings, spec: JobSpec) -> dict[str, str]:
    if spec.job_id is None or spec.sealed is None:
        raise JobError("CREDENTIALS_MISSING")
    try:
        data = json.loads(box_of(settings).open(spec.sealed, credentials_aad(spec.job_id)))
    except (SecretsError, ValueError) as exc:
        raise JobError("CREDENTIALS_UNREADABLE") from exc
    if not isinstance(data, dict):
        raise JobError("CREDENTIALS_UNREADABLE")
    return {str(k): str(v) for k, v in data.items()}
