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
from app.db.models import AppSetting, BackupRootKey, StorageRoot

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


# ------------------------------------------------------------------ per-root keys (ADR-016)
#
# The shared tree keeps using the administrator's instance key above. Every personal root has its
# own key chain: the private half is shown once to the owner and never stored; the database keeps
# the public half and its fingerprint. A new key is `pending` until the owner proves they saved it
# by presenting it back; only then does it become the one `active` key and does the root back up
# again. Every change locks the root's row, so two requests cannot both create or rotate its key.


class RootKeyError(Exception):
    """Base class; `code` is an API error code."""

    code = "ROOT_KEY_ERROR"


class NoPendingKey(RootKeyError):
    code = "NO_PENDING_KEY"


class EscrowUnavailable(RootKeyError):
    code = "ESCROW_UNAVAILABLE"


class NoActiveKey(RootKeyError):
    code = "NO_ACTIVE_KEY"


class KeyMismatch(RootKeyError):
    code = "BACKUP_KEY_MISMATCH"


def _lock_root(db: Session, root_id: Any) -> StorageRoot:
    root = db.scalar(select(StorageRoot).where(StorageRoot.id == root_id).with_for_update())
    if root is None:
        raise RootKeyError("root not found")
    return root


def active_root_key(db: Session, root_id: Any) -> BackupRootKey | None:
    return db.scalar(
        select(BackupRootKey).where(
            BackupRootKey.root_id == root_id, BackupRootKey.state == "active"
        )
    )


def pending_root_key(db: Session, root_id: Any) -> BackupRootKey | None:
    return db.scalar(
        select(BackupRootKey).where(
            BackupRootKey.root_id == root_id, BackupRootKey.state == "pending"
        )
    )


def root_key_fingerprints(db: Session, root_id: Any) -> set[str]:
    """Fingerprints of every key this root ever had (any state): what makes a chain 'its own'."""
    return set(
        db.scalars(select(BackupRootKey.fingerprint).where(BackupRootKey.root_id == root_id))
    )


def key_for_root(db: Session, root: StorageRoot) -> KeyInfo | None:
    """The key a run of this root encrypts to: the instance key for the shared tree, the owner's
    confirmed key for a personal root. A key the owner has not confirmed is never used."""
    if root.kind == "shared":
        return load_key(db)
    row = active_root_key(db, root.id)
    if row is None:
        return None
    return KeyInfo(row.public, row.fingerprint, row.created_at.isoformat())


def begin_root_key(db: Session, root_id: Any, expected_fingerprint: str | None) -> tuple[str, str]:
    """Makes a pending key pair for the root and returns (recovery key text, fingerprint) to show
    once. Replaces an earlier pending key (nothing was ever encrypted to it). Compare-and-swap on
    the active key: refused with KeyChanged unless the root's active key is still the one the
    caller saw (None when it has none), so a stale page cannot rotate a newer key away."""
    _lock_root(db, root_id)
    active = active_root_key(db, root_id)
    if (active.fingerprint if active else None) != expected_fingerprint:
        raise KeyChanged
    old = pending_root_key(db, root_id)
    if old is not None:
        db.delete(old)
        db.flush()
    recovery, public, fp = crypto.generate_keypair()
    db.add(
        BackupRootKey(
            root_id=root_id,
            public=public,
            fingerprint=fp,
            state="pending",
            created_at=clock.utcnow(),
        )
    )
    db.flush()
    return recovery, fp


def _seal_for(
    db: Session, root_id: Any, recovery_key: str, fp: str, admin: KeyInfo | None
) -> tuple[bytes, str]:
    if admin is None:
        raise EscrowUnavailable
    blob = crypto.seal(
        crypto.parse_public_key(admin.public),
        recovery_key.strip().encode(),
        crypto.escrow_context(root_id, fp),
    )
    return blob, admin.fingerprint


def confirm_root_key(
    db: Session,
    root_id: Any,
    recovery_key: str,
    *,
    escrow: bool,
    admin: KeyInfo | None,
) -> BackupRootKey:
    """The owner presented the key back: it is proven saved. The pending key becomes the active
    one (the previous active key is retired but kept: it still describes its chains)."""
    _lock_root(db, root_id)
    fp = crypto.recovery_key_fingerprint(recovery_key)  # BadKey if malformed
    pending = pending_root_key(db, root_id)
    if pending is None or pending.fingerprint != fp:
        raise NoPendingKey
    now = clock.utcnow()
    blob = efp = None
    if escrow:
        blob, efp = _seal_for(db, root_id, recovery_key, fp, admin)
    previous = active_root_key(db, root_id)
    if previous is not None:
        previous.state, previous.retired_at = "retired", now
        db.flush()
    pending.state, pending.confirmed_at = "active", now
    if blob is not None:
        pending.escrow_blob, pending.escrow_key_fingerprint, pending.escrow_changed_at = (
            blob,
            efp,
            now,
        )
    db.flush()
    return pending


def set_escrow(
    db: Session, root_id: Any, *, enabled: bool, recovery_key: str | None, admin: KeyInfo | None
) -> BackupRootKey:
    """Turns administrator escrow on (the owner presents their key, which is sealed to the
    administrator key) or off (the sealed copy is deleted; copies the administrator already made
    cannot be recalled)."""
    _lock_root(db, root_id)
    row = active_root_key(db, root_id)
    if row is None:
        raise NoActiveKey
    now = clock.utcnow()
    if enabled:
        if recovery_key is None or crypto.recovery_key_fingerprint(recovery_key) != row.fingerprint:
            raise KeyMismatch
        row.escrow_blob, row.escrow_key_fingerprint = _seal_for(
            db, root_id, recovery_key, row.fingerprint, admin
        )
    else:
        row.escrow_blob = row.escrow_key_fingerprint = None
    row.escrow_changed_at = now
    db.flush()
    return row
