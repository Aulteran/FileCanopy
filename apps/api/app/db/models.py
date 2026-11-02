import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
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

# Node statuses that behave as nonexistent for every user route, scan and backup. Dismissed nodes
# and trashed or purged ones (ADR-017) keep their rows for history but release their names.
HIDDEN_NODE_STATUSES = ("dismissed", "trashed", "purged")

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
    # M10: the person lets this server open Private and Confidential files for previews, text
    # extraction and thumbnails. Off by default; never involves a cloud service.
    local_processing_opt_in: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # M15: how this person's personal tree uses AI: off | instance_default |
    # instance_default_local_only | my_provider.
    ai_preference: Mapped[str] = mapped_column(
        String(32), nullable=False, default="off", server_default="off"
    )
    # What the person acknowledged about the server's cloud AI (provider row, vendor, notice
    # version); routing to a different cloud provider needs a new acknowledgement.
    ai_ack: Mapped[str | None] = mapped_column(String(120), nullable=True)


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
            sqlite_where=text("status NOT IN ('dismissed','missing','trashed','purged')"),
            postgresql_where=text("status NOT IN ('dismissed','missing','trashed','purged')"),
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
    )  # active | busy | reconcile_needed | missing | dismissed | trashed | purged
    # The trash item that holds this node (and its subtree) while status is trashed or purged;
    # a nested item trashed earlier keeps its own id, so restoring the outer one cannot revive it.
    trash_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True, nullable=True)
    # Folders only: normal | private | confidential. The effective label is the most restrictive
    # one on the node or any ancestor (M09); it is never copied down, so a move re-labels by place.
    privacy_label: Mapped[str] = mapped_column(
        String(12), nullable=False, default="normal", server_default="normal"
    )
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


class Notification(Base):
    """In-app notice for one recipient (M08). Holds ids and a kind only, never names or paths: the
    text is built when it is read, and a folder name is shown only while the reader can still open
    that folder, so a notice cannot outlive the access it talks about."""

    __tablename__ = "notification"
    __table_args__ = (Index("ix_notification_recipient_at", "recipient_user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # shared|unshared|share_left
    node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # M14 (mail kinds): how many files or messages, and a static reason code. Never names.
    count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


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
    # M12b: the lock screen. While set, every request but unlock and sign-out is refused.
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # M12b: this session just re-authenticated through the OIDC provider (single use proof).
    reauth_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # True only when the provider vouched (`auth_time`) that the person signed in just now. A PIN
    # change needs this; turning on provider-only sign-in does not.
    reauth_strong: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )


class UserPin(Base):
    """Six-digit Confidential PIN (M12b). `pin_hash` is Argon2id over an HMAC of the PIN with a
    server-side pepper. The attempt counter belongs to the account, not to a session or address."""

    __tablename__ = "user_pin"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )
    pin_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    set_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_failure_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    blocked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Too many wrong guesses: unusable until reset with the password, the provider or a mail link.
    disabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )


class ConfidentialUnlock(Base):
    """A short-lived unlock of one Confidential subtree, bound to one login session (M09). Created
    only after the server verified the account password; renewed only by interactive use."""

    __tablename__ = "confidential_unlock"
    __table_args__ = (UniqueConstraint("session_id", "scope_node_id", name="uq_unlock_scope"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("auth_session.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    scope_node_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


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


class UserIdentity(Base):
    """A provider identity linked to one account (M12, ADR-020). The key is (issuer, subject);
    `email_seen` is informational and is never used to find or merge accounts."""

    __tablename__ = "user_identity"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_identity_issuer_subject"),
        UniqueConstraint("user_id", "issuer", name="uq_identity_user_issuer"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email_seen: Mapped[str | None] = mapped_column(String(254), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class OidcLogin(Base):
    """One pending OIDC round trip: single use, ten minutes, bound to the starting browser by a
    cookie. Holds the PKCE verifier and nonce; an invitation also holds the chosen password's
    Argon2 hash and the invitation token's digest."""

    __tablename__ = "oidc_login"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(8), nullable=False)  # login|link|invite|reauth
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(512), nullable=True)
    new_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
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
    """Append-only per-node activity log (ADR-011 §9, ADR-017). `details` hold ids, safe names and
    an uncertainty flag: never host paths, content or email addresses. `correlation_id` is the
    `file_operation` (or import batch) the event belongs to; an `ai_agent` event names the user it
    acted for in `on_behalf_of_user_id` and never an `actor_user_id`."""

    __tablename__ = "file_event"
    __table_args__ = (Index("ix_file_event_node_at", "node_id", "at"),)

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True, nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime, index=True, nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # ui|import|scan|watcher|system
    details: Mapped[Any] = mapped_column(Json, nullable=False)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True, nullable=True)
    on_behalf_of_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class TrashItem(Base):
    """One trashed top entry (ADR-017 §2). The on-disk location is derived from `root_id` and `id`;
    no host path is stored. Visible per ADR-017 §3 only."""

    __tablename__ = "trash_item"
    __table_args__ = (Index("ix_trash_item_root_status", "root_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    root_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("storage_root.id"), nullable=False)
    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True, nullable=False)
    original_parent_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    trashed_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app_user.id"), nullable=False)
    trashed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # deleted | moved_out | import_undone | copy_undone
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    # Null only while automatic purging is off (retention 0); a later setting backfills it.
    purge_after: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # in_trash | busy (restore or purge running) | restored | purged | reconcile_needed
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


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


class BackupRun(Base):
    """One backup run of one root (ADR-015). Counts and sizes only: never names, paths, content or
    keys. Only `ok` rows that are not `pruned_at` take part in chains and retention."""

    __tablename__ = "backup_run"
    __table_args__ = (Index("ix_backup_run_root_started", "root_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    root_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("storage_root.id"), index=True, nullable=False
    )
    chain_id: Mapped[str] = mapped_column(String(40), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # full | incremental
    # running | ok | failed | refused (could not start: volume, size limit)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    archive_name: Mapped[str | None] = mapped_column(String(160), nullable=True)  # relative
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    uncompressed_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    archive_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    files_added: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_missing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_unstable: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    key_fingerprint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    event_high_water: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    pruned_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class BackupChainState(Base):
    """What the newest verified run of a root contained (path -> size, mtime, inode), so the next
    run can find changes without reading the (encrypted) archives. Private to the app database."""

    __tablename__ = "backup_chain_state"

    root_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("storage_root.id"), primary_key=True
    )
    chain_id: Mapped[str] = mapped_column(String(40), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # zlib JSON
    encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    key_fingerprint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_archive: Mapped[str] = mapped_column(String(160), nullable=False)
    event_high_water: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class SearchDoc(Base):
    """Extracted text of one file, for full-text search (M11). A derivative: it is only ever read
    through `app.search.query`, which re-decides access per person. The row records which version
    of the file it came from; a mismatch hides it until the indexer replaces it."""

    __tablename__ = "search_doc"

    node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("node.id", ondelete="CASCADE"), primary_key=True
    )
    root_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False)  # text | ocr
    body: Mapped[str] = mapped_column(Text, nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class SearchJob(Base):
    """One indexing job per file. Leased while running, so a crashed worker's job is taken again."""

    __tablename__ = "search_job"

    node_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("node.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(
        String(8), index=True, nullable=False
    )  # queued|running|failed
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(String(32), nullable=True)


class UserEmailAlias(Base):
    """An extra sender address for one account (M13). Pending until the mailed link is used; a
    verified address is unique across accounts. The account's own `email` is also a sender
    identity but lives on the user row."""

    __tablename__ = "user_email_alias"
    __table_args__ = (
        UniqueConstraint("user_id", "email", name="uq_alias_user_email"),
        Index(
            "uq_alias_verified_email",
            "email",
            unique=True,
            sqlite_where=text("verified_at IS NOT NULL"),
            postgresql_where=text("verified_at IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False)  # normalized
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class InboundMessage(Base):
    """One message seen in the inbound mailbox (M13, ADR-007). Metadata only: no body is stored.
    status: verified | quarantined | rejected | duplicate | error."""

    __tablename__ = "inbound_message"
    __table_args__ = (
        UniqueConstraint("mailbox_key", "uidvalidity", "uid", name="uq_inbound_uid"),
        Index("ix_inbound_status_at", "status", "received_at"),
        Index("ix_inbound_sha", "content_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    mailbox_key: Mapped[str] = mapped_column(String(32), nullable=False)
    uidvalidity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message_id_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    from_address: Mapped[str | None] = mapped_column(String(254), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    destination: Mapped[str] = mapped_column(String(12), nullable=False, default="unknown")
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resolved_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="SET NULL"), index=True, nullable=True
    )
    auth_summary: Mapped[Any] = mapped_column(Json, nullable=False, default=dict)
    attachment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # none (nothing kept) | stored | dropped (over quota) | purged (retention or rejection)
    content_state: Mapped[str] = mapped_column(String(8), nullable=False, default="none")
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # M14: the queue for `verified` messages. null (not verified) | pending | filed | partial |
    # failed. `process_after` is both the retry time and the claim lease while a worker has it.
    processing_state: Mapped[str | None] = mapped_column(String(8), nullable=True)
    process_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    process_after: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    process_error: Mapped[str | None] = mapped_column(String(40), nullable=True)
    filed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    filed_node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    manual_retries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class InboundAttachment(Base):
    """Attachment metadata. `display_name` is for display only; stored bytes use `stored_name`."""

    __tablename__ = "inbound_attachment"
    __table_args__ = (UniqueConstraint("message_id", "position", name="uq_attachment_position"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("inbound_message.id", ondelete="CASCADE"), index=True, nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    stored_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # M14: the file this became, or a refusal code (ARCHIVE_*, TOO_LARGE, NOT_KEPT ...).
    node_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)


class MailboxState(Base):
    """Checkpoint, lease and retry bookkeeping for one polled mailbox."""

    __tablename__ = "mailbox_state"

    mailbox_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    uidvalidity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_uid: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_poll_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(40), nullable=True)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_uid: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AiProvider(Base):
    """A configured AI provider (M15). `secret_ciphertext` is AES-GCM sealed; no API returns it."""

    __tablename__ = "ai_provider"
    __table_args__ = (
        Index(
            "uq_ai_provider_instance",
            "scope",
            unique=True,
            sqlite_where=text("scope = 'instance'"),
            postgresql_where=text("scope = 'instance'"),
        ),
        Index(
            "uq_ai_provider_user",
            "owner_user_id",
            unique=True,
            sqlite_where=text("scope = 'user'"),
            postgresql_where=text("scope = 'user'"),
        ),
        CheckConstraint("scope in ('instance', 'user')", name="ck_ai_provider_scope"),
        CheckConstraint("privacy_mode in ('local', 'cloud')", name="ck_ai_provider_mode"),
        CheckConstraint(
            "(scope = 'instance') = (owner_user_id is null)", name="ck_ai_provider_owner"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(8), nullable=False)  # instance | user
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # anthropic | openai | gemini | ollama | openai_compatible
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    privacy_mode: Mapped[str] = mapped_column(String(8), nullable=False)  # local | cloud
    label: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    endpoint: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    # Micro-dollars per million tokens, entered by the person; 0 for local providers.
    price_in_micro: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    price_out_micro: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The version of the privacy notice acknowledged before a cloud provider was enabled.
    consent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    consent_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class AiBudget(Base):
    """One scope's spending in one calendar month, in micro-dollars (reserve, then settle)."""

    __tablename__ = "ai_budget"
    __table_args__ = (UniqueConstraint("scope_key", "month", name="uq_ai_budget_scope_month"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scope_key: Mapped[str] = mapped_column(String(48), nullable=False)  # instance | user:<uuid>
    month: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM (UTC)
    cap_micro: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spent_micro: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_micro: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class AiUsage(Base):
    """Ledger of AI requests. Never holds prompts, replies, file names or document text."""

    __tablename__ = "ai_usage"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    month: Mapped[str] = mapped_column(String(7), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    provider_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    # reserved | ok | failed | refused | expired
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reserved_micro: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    final_micro: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
