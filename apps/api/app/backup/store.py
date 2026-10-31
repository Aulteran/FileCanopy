"""Admin-controlled backup settings and the public key, kept in `app_setting` (ADR-015 section 1).
Nothing secret is stored: the recovery key is shown once and never written anywhere. Callers own
the transaction."""

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import clock
from app.backup import crypto
from app.db.models import AppSetting

SETTINGS_KEY = "backup_settings"
KEY_KEY = "backup_key"

INTERVAL_RANGE = (5, 525_600)  # minutes
FULL_EVERY_RANGE = (1, 365)
MIN_CHAINS_RANGE = (1, 50)


@dataclass(frozen=True)
class BackupConfig:
    enabled: bool = False
    interval_minutes: int = 1440
    full_every: int = 7
    min_chains_kept: int = 2
    max_size_bytes: int | None = None
    encrypt: bool = True


def _int_in(value: Any, low: int, high: int, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return default
    return value


def load_config(db: Session) -> BackupConfig:
    """Fails closed: a damaged row means defaults, which means disabled."""
    row = db.get(AppSetting, SETTINGS_KEY)
    raw: Any = row.value if row is not None else {}
    if not isinstance(raw, dict):
        return BackupConfig()
    base = BackupConfig()
    size = raw.get("max_size_bytes")
    return BackupConfig(
        enabled=raw.get("enabled") is True,
        interval_minutes=_int_in(
            raw.get("interval_minutes"), *INTERVAL_RANGE, base.interval_minutes
        ),
        full_every=_int_in(raw.get("full_every"), *FULL_EVERY_RANGE, base.full_every),
        min_chains_kept=_int_in(
            raw.get("min_chains_kept"), *MIN_CHAINS_RANGE, base.min_chains_kept
        ),
        max_size_bytes=size
        if isinstance(size, int) and not isinstance(size, bool) and size > 0
        else None,
        encrypt=raw.get("encrypt") is not False,
    )


def save_config(db: Session, cfg: BackupConfig) -> None:
    value: Any = asdict(cfg)
    row = db.get(AppSetting, SETTINGS_KEY)
    if row is None:
        db.add(AppSetting(key=SETTINGS_KEY, value=value))
    else:
        row.value = value
    db.flush()


@dataclass(frozen=True)
class KeyInfo:
    public: str
    fingerprint: str
    created_at: str


def load_key(db: Session) -> KeyInfo | None:
    row = db.get(AppSetting, KEY_KEY)
    if row is None or not isinstance(row.value, dict):
        return None
    v = row.value
    try:
        crypto.parse_public_key(str(v["public"]))
        return KeyInfo(str(v["public"]), str(v["fingerprint"]), str(v["created_at"]))
    except (KeyError, crypto.BadKey):
        return None


class KeyChanged(Exception):
    """The key is not the one the caller expected (someone else made or rotated it first)."""


def _lock_key_row(db: Session) -> AppSetting | None:
    """The key row, locked for the rest of the transaction (`SELECT ... FOR UPDATE` on
    PostgreSQL), so two requests cannot both create or rotate the key."""
    return db.scalar(select(AppSetting).where(AppSetting.key == KEY_KEY).with_for_update())


def _store_key(db: Session, row: AppSetting | None) -> tuple[str, str]:
    recovery, public, fp = crypto.generate_keypair()
    value: Any = {"public": public, "fingerprint": fp, "created_at": clock.utcnow().isoformat()}
    if row is None:
        db.add(AppSetting(key=KEY_KEY, value=value))
    else:
        row.value = value
    db.flush()
    return recovery, fp


def create_key_if_absent(db: Session) -> tuple[str, str] | None:
    """Creates the instance key pair when there is none and returns (recovery key text,
    fingerprint) to show once; returns None when a key already exists (including one that a
    concurrent request just made: the primary key stops a second insert)."""
    row = _lock_key_row(db)
    if row is not None and load_key(db) is not None:
        return None
    try:
        with db.begin_nested():
            return _store_key(db, row)
    except IntegrityError:
        return None  # lost the race: the other request owns the one-time key


def rotate_key(db: Session, expected_fingerprint: str | None = None) -> tuple[str, str]:
    """Replaces the key pair. With `expected_fingerprint` (compare-and-swap) the rotation is
    refused with KeyChanged unless the stored key is still that one."""
    row = _lock_key_row(db)
    current = load_key(db)
    if expected_fingerprint is not None and (
        current is None or current.fingerprint != expected_fingerprint
    ):
        raise KeyChanged
    return _store_key(db, row)
