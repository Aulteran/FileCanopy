"""PDF split proposals

Revision ID: 0020
Revises: 0019

M17. `split_proposal` holds one open split proposal per file (partial unique index).
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps the tables.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Undo of a split trashes the parts it made with reason `split_undone`.
        op.execute("ALTER TABLE trash_item DROP CONSTRAINT IF EXISTS ck_trash_item_reason")
        op.create_check_constraint(
            "ck_trash_item_reason",
            "trash_item",
            "reason in ('deleted','moved_out','import_undone','copy_undone','split_undone')",
        )
    if "split_proposal" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "split_proposal",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Uuid,
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "node_id",
            sa.Uuid,
            sa.ForeignKey("node.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("status", sa.String(12), nullable=False, index=True),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("page_count", sa.Integer, nullable=False),
        sa.Column("parts", sa.JSON, nullable=False),
        sa.Column("blank_pages", sa.JSON, nullable=False),
        sa.Column("left_out", sa.JSON, nullable=False),
        sa.Column("warnings", sa.JSON, nullable=False),
        sa.Column("reason", sa.String(200), nullable=False, server_default=""),
        sa.Column("confidence", sa.Integer, nullable=False, server_default="0"),
        sa.Column("snapshot", sa.JSON, nullable=False),
        sa.Column("outputs", sa.JSON, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("decided_at", TS),
        sa.Column("decided_by", sa.Uuid),
        sa.Column("outcome", sa.String(40), nullable=False, server_default=""),
    )
    open_only = sa.text("status IN ('pending','applying')")
    op.create_index(
        "uq_split_proposal_open",
        "split_proposal",
        ["node_id"],
        unique=True,
        sqlite_where=open_only,
        postgresql_where=open_only,
    )


def downgrade() -> None:
    op.drop_table("split_proposal")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE trash_item DROP CONSTRAINT IF EXISTS ck_trash_item_reason")
        op.create_check_constraint(
            "ck_trash_item_reason",
            "trash_item",
            "reason in ('deleted','moved_out','import_undone','copy_undone')",
        )
