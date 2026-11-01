"""Sender aliases and the inbound mailbox

Revision ID: 0016
Revises: 0015

M13 (ADR-007). `user_email_alias` holds extra sender addresses (a verified address is unique);
`inbound_message`, `inbound_attachment` and `mailbox_state` hold what the mailbox poller saw.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps the tables.
    names = set(sa.inspect(op.get_bind()).get_table_names())
    if "user_email_alias" not in names:
        op.create_table(
            "user_email_alias",
            sa.Column("id", sa.Uuid, primary_key=True),
            sa.Column(
                "user_id",
                sa.Uuid,
                sa.ForeignKey("app_user.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("email", sa.String(254), nullable=False),
            sa.Column("created_at", TS, nullable=False),
            sa.Column("verified_at", TS),
            sa.Column("token_hash", sa.String(64), unique=True),
            sa.Column("token_expires_at", TS),
            sa.UniqueConstraint("user_id", "email", name="uq_alias_user_email"),
        )
        op.create_index("ix_user_email_alias_user_id", "user_email_alias", ["user_id"])
        op.create_index(
            "uq_alias_verified_email",
            "user_email_alias",
            ["email"],
            unique=True,
            postgresql_where=sa.text("verified_at IS NOT NULL"),
            sqlite_where=sa.text("verified_at IS NOT NULL"),
        )
    if "inbound_message" not in names:
        op.create_table(
            "inbound_message",
            sa.Column("id", sa.Uuid, primary_key=True),
            sa.Column("mailbox_key", sa.String(32), nullable=False),
            sa.Column("uidvalidity", sa.BigInteger, nullable=False),
            sa.Column("uid", sa.BigInteger, nullable=False),
            sa.Column("content_sha256", sa.String(64)),
            sa.Column("message_id_hash", sa.String(64)),
            sa.Column("received_at", TS, nullable=False),
            sa.Column("from_address", sa.String(254)),
            sa.Column("subject", sa.String(300)),
            sa.Column("size_bytes", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("destination", sa.String(12), nullable=False, server_default="unknown"),
            sa.Column("status", sa.String(12), nullable=False),
            sa.Column("reason", sa.String(40)),
            sa.Column(
                "resolved_user_id",
                sa.Uuid,
                sa.ForeignKey("app_user.id", ondelete="SET NULL"),
            ),
            sa.Column("auth_summary", sa.JSON, nullable=False),
            sa.Column("attachment_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("content_state", sa.String(8), nullable=False, server_default="none"),
            sa.Column("duplicate_of", sa.Uuid),
            sa.Column("created_at", TS, nullable=False),
            sa.UniqueConstraint("mailbox_key", "uidvalidity", "uid", name="uq_inbound_uid"),
        )
        op.create_index("ix_inbound_status_at", "inbound_message", ["status", "received_at"])
        op.create_index("ix_inbound_sha", "inbound_message", ["content_sha256"])
        op.create_index(
            "ix_inbound_message_resolved_user_id", "inbound_message", ["resolved_user_id"]
        )
    if "inbound_attachment" not in names:
        op.create_table(
            "inbound_attachment",
            sa.Column("id", sa.Uuid, primary_key=True),
            sa.Column(
                "message_id",
                sa.Uuid,
                sa.ForeignKey("inbound_message.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("position", sa.Integer, nullable=False),
            sa.Column("display_name", sa.String(255), nullable=False),
            sa.Column("content_type", sa.String(100), nullable=False),
            sa.Column("size_bytes", sa.BigInteger, nullable=False),
            sa.Column("sha256", sa.String(64), nullable=False),
            sa.Column("stored_name", sa.String(40)),
            sa.UniqueConstraint("message_id", "position", name="uq_attachment_position"),
        )
        op.create_index("ix_inbound_attachment_message_id", "inbound_attachment", ["message_id"])
    if "mailbox_state" not in names:
        op.create_table(
            "mailbox_state",
            sa.Column("mailbox_key", sa.String(32), primary_key=True),
            sa.Column("uidvalidity", sa.BigInteger),
            sa.Column("last_uid", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("lease_until", TS),
            sa.Column("last_poll_at", TS),
            sa.Column("last_error", sa.String(40)),
            sa.Column("failures", sa.Integer, nullable=False, server_default="0"),
            sa.Column("retry_uid", sa.BigInteger),
            sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.drop_table("mailbox_state")
    op.drop_table("inbound_attachment")
    op.drop_table("inbound_message")
    op.drop_table("user_email_alias")
