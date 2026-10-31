"""node revision/size and the file operation journal

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("node", sa.Column("revision", sa.Integer, nullable=False, server_default="1"))
    op.add_column("node", sa.Column("size", sa.BigInteger, nullable=True))
    op.create_table(
        "file_operation",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("actor_user_id", sa.Uuid, sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("node_id", sa.Uuid),
        sa.Column("target_parent_id", sa.Uuid),
        sa.Column("detail", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint(
            "status in ('planned','committed','failed','rolled_back','reconcile_needed')",
            name="ck_file_operation_status",
        ),
        sa.UniqueConstraint("actor_user_id", "idempotency_key", name="uq_file_operation_key"),
    )
    op.create_index("ix_file_operation_status", "file_operation", ["status"])
    op.create_index("ix_file_operation_node_id", "file_operation", ["node_id"])


def downgrade() -> None:
    op.drop_table("file_operation")
    op.drop_column("node", "size")
    op.drop_column("node", "revision")
