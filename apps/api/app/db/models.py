import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

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
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # active | missing | disabled
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class Node(Base):
    """Stable identity of a folder or file. The API only ever addresses nodes by `id`;
    `lineage` is the '/'-delimited ids of all ancestors including self (ADR-004 §2)."""

    __tablename__ = "node"
    __table_args__ = (
        UniqueConstraint("parent_id", "name", name="uq_node_parent_name"),
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
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # dir | file
    lineage: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
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
