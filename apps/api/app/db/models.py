import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.storage.names import name_key

BigPK = BigInteger().with_variant(Integer(), "sqlite")  # SQLite autoincrements INTEGER only
Json = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on every backend (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("naive datetime")
        return value.astimezone(UTC) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    pass


class AppSetting(Base):
    """Instance-level key/value metadata. Documents themselves never live in the database."""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(Json, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(Base):
    """Local account. `password_hash` is Argon2id and is never serialized by any API."""

    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)  # normalized
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # admin | member
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # invited | active | disabled
    password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class StorageRoot(Base):
    """A top-level directory under the library mount: the shared tree or one personal root."""

    __tablename__ = "storage_root"
    __table_args__ = (
        Index(
            "uq_storage_root_one_shared",
            "kind",
            unique=True,
            sqlite_where=text("kind = 'shared'"),
            postgresql_where=text("kind = 'shared'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # shared | personal
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id"), unique=True, nullable=True
    )
    relative_dir: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    # active | missing | disabled | unsupported_mount (ADR-011 §3: not on the library device)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    scan_state: Mapped[str | None] = mapped_column(String(16), nullable=True)  # null | suspended
    # open | closed (ADR-011 §1): the one-time window for a setup import; closed is final.
    setup_state: Mapped[str] = mapped_column(
        String(8), nullable=False, default="open", server_default="open"
    )
    # Set while the reconciler skips this root because an import is writing into it; cleared by
    # the first pass that finds no import writing. Bounded by `scan_pause_ceiling_seconds`.
    scan_paused_since: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    fs_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    network_fs: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )


class Node(Base):
    """Stable identity of a folder or file. The API only ever addresses nodes by `id`;
    `lineage` is the '/'-delimited ids of all ancestors including self (ADR-004 §2)."""

    __tablename__ = "node"
    __table_args__ = (
        # Dismissed and missing nodes stay for audit/recovery but release their name, so a
        # different kind of entry can take it on disk (ADR-011 §7). The app still avoids the
        # names of missing nodes.
        Index(
            "uq_node_parent_name",
            "parent_id",
            "name",
            unique=True,
            sqlite_where=text("status NOT IN ('dismissed','missing')"),
            postgresql_where=text("status NOT IN ('dismissed','missing')"),
        ),
        Index("ix_node_parent_name_key", "parent_id", "name_key"),
        Index(
            "uq_node_one_root",
            "root_id",
            unique=True,
            sqlite_where=text("parent_id IS NULL"),
            postgresql_where=text("parent_id IS NULL"),
        ),
        Index("ix_node_lineage", "lineage", postgresql_ops={"lineage": "text_pattern_ops"}),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    root_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("storage_root.id"), index=True, nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("node.id"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # dir | file | other
    lineage: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # active | busy | reconcile_needed | missing | dismissed
    # Optimistic-concurrency token: bumped by every rename and move (ADR-013).
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # files only
    # Last observed filesystem identity (ADR-011 §6). Null until the first scan or app write.
    fs_dev: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fs_ino: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mtime_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # SHA-256 hex
    # casefold(NFC(name)), not unique; derived from `name` on insert
    name_key: Mapped[str] = mapped_column(
        Text, nullable=False, default=lambda ctx: name_key(ctx.get_current_parameters()["name"])
    )
    unsupported_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    raw_name_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen_run: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class FileOperation(Base):
    """Journal row for one mutating file action (04_DATA_FILESYSTEM.md, action contract step 5).
    `detail` holds ids, names, step names and error codes: never file content or absolute paths."""

    __tablename__ = "file_operation"
    __table_args__ = (
        UniqueConstraint("actor_user_id", "idempotency_key", name="uq_file_operation_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True, nullable=True)
    target_parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    detail: Mapped[Any] = mapped_column(Json, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class NodeGrant(Base):
    """Folder grant: `level` applies to the folder and its whole subtree, never upward."""

    __tablename__ = "node_grant"
    __table_args__ = (UniqueConstraint("node_id", "grantee_user_id", name="uq_node_grant"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("node.id", ondelete="CASCADE"), index=True, nullable=False
    )
    grantee_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id"), index=True, nullable=False
    )
    level: Mapped[str] = mapped_column(String(8), nullable=False)  # viewer | editor
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("app_user.id"),
        nullable=True,  # None = instance default grant
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class AuthSession(Base):
    """Server-side session. Only the SHA-256 of the cookie token is stored."""

    __tablename__ = "auth_session"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)


class AuthToken(Base):
    """Single-use invitation / password-reset token (hash only)."""

    __tablename__ = "auth_token"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)  # invitation | reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class LoginFailure(Base):
    """One failed authentication attempt, keyed by a hash (never the raw email or IP)."""

    __tablename__ = "login_failure"
    __table_args__ = (Index("ix_login_failure_key_at", "key_hash", "attempted_at"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class AuthAudit(Base):
    """Append-only security audit. `details` must never hold secrets or plaintext emails."""

    __tablename__ = "auth_audit"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[Any] = mapped_column(Json, nullable=False)


class ScanRun(Base):
    """One reconciler pass over a root (ADR-011 §7). `counts` holds change tallies only."""

    __tablename__ = "scan_run"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    root_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("storage_root.id"), index=True, nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)  # startup|periodic|manual|hint
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)  # running|ok|partial|...
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    counts: Mapped[Any] = mapped_column(Json, nullable=True)


class FileEvent(Base):
    """Minimal append-only per-node event log (ADR-011 §9); M07 builds the activity UI on it.
    `details` hold ids, safe names and an uncertainty flag: never host paths or content."""

    __tablename__ = "file_event"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True, nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime, index=True, nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # ui|import|scan|watcher
    details: Mapped[Any] = mapped_column(Json, nullable=False)


class ImportBatch(Base):
    """One setup import or folder upload (ADR-011 §2). `plan` holds totals and example names the
    importer supplied themselves, never host paths. Visible to its actor only."""

    __tablename__ = "import_batch"
    __table_args__ = (
        # At most one unfinished setup import per root, whatever the request timing.
        Index(
            "uq_import_batch_open_setup",
            "root_id",
            unique=True,
            sqlite_where=text(
                "kind in ('setup_folder','setup_zip') and status in ('planned','running','stopped')"
            ),
            postgresql_where=text(
                "kind in ('setup_folder','setup_zip') and status in ('planned','running','stopped')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    root_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("storage_root.id"), index=True, nullable=False
    )
    target_node_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id"), index=True, nullable=False
    )
    # setup_folder | setup_zip | folder_upload
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # planned | running | done | stopped | failed | cancelled | expired
    status: Mapped[str] = mapped_column(String(12), index=True, nullable=False)
    include_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    plan: Mapped[Any] = mapped_column(Json, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    staged_zip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Set when the retention job deleted this batch's import_entry rows (the summary stays).
    entries_pruned_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class ImportEntry(Base):
    """Journal row for one folder or file of a batch: pending -> writing -> done | renamed |
    skipped | failed (ADR-011 §2). `idx` is stable for the life of the batch."""

    __tablename__ = "import_entry"
    __table_args__ = (
        UniqueConstraint("batch_id", "idx", name="uq_import_entry_idx"),
        Index("ix_import_entry_batch_status", "batch_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("import_batch.id", ondelete="CASCADE"), index=True, nullable=False
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # cleaned; "" when skipped
    path: Mapped[str] = mapped_column(Text, nullable=False)  # as the importer sent it, sanitized
    kind: Mapped[str] = mapped_column(String(4), nullable=False)  # dir | file
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mtime_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    src_index: Mapped[int | None] = mapped_column(Integer, nullable=True)  # position in the zip
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)  # skip or error code
    node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    result_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dir_node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    tmp_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dst_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
