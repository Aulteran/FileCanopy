"""auth: users, sessions, single-use tokens, login throttling, security audit

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("password_hash", sa.String(512)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("password_changed_at", TS),
        sa.CheckConstraint("role in ('admin','member')", name="ck_app_user_role"),
        sa.CheckConstraint("status in ('invited','active','disabled')", name="ck_app_user_status"),
    )
    op.create_table(
        "auth_session",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "user_id", sa.Uuid, sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS),
        sa.Column("user_agent", sa.String(200)),
    )
    op.create_index("ix_auth_session_user_id", "auth_session", ["user_id"])
    op.create_table(
        "auth_token",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "user_id", sa.Uuid, sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by", sa.Uuid),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS),
        sa.CheckConstraint("purpose in ('invitation','reset')", name="ck_auth_token_purpose"),
    )
    op.create_index("ix_auth_token_user_id", "auth_token", ["user_id"])
    op.create_table(
        "login_failure",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("attempted_at", TS, nullable=False),
    )
    op.create_index("ix_login_failure_key_at", "login_failure", ["key_hash", "attempted_at"])
    op.create_table(
        "auth_audit",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("at", TS, nullable=False),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("actor_user_id", sa.Uuid),
        sa.Column("target_user_id", sa.Uuid),
        sa.Column("ip", sa.String(64)),
        sa.Column("details", JSONB, nullable=False),
    )
    op.create_index("ix_auth_audit_at", "auth_audit", ["at"])


def downgrade() -> None:
    for table in ("auth_audit", "login_failure", "auth_token", "auth_session", "app_user"):
        op.drop_table(table)
