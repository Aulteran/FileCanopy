"""Virtual collections and AI grouping suggestions

Revision ID: 0021
Revises: 0020

M18. References only: `collection_member` stores node ids, never paths or content.
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps the tables.
    bind = op.get_bind()
    have = set(sa.inspect(bind).get_table_names())
    if "collection" in have:
        return
    op.create_table(
        "collection",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Uuid,
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("title_key", sa.String(80), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("suggestion_id", sa.Uuid),
        sa.UniqueConstraint("owner_user_id", "title_key", name="uq_collection_title"),
    )
    op.create_table(
        "collection_member",
        sa.Column(
            "collection_id",
            sa.Uuid,
            sa.ForeignKey("collection.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "node_id",
            sa.Uuid,
            sa.ForeignKey("node.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
        sa.Column("added_by", sa.Uuid),
        sa.Column("added_at", TS, nullable=False),
    )
    op.create_table(
        "collection_share",
        sa.Column(
            "collection_id",
            sa.Uuid,
            sa.ForeignKey("collection.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid,
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_table(
        "collection_suggestion",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Uuid,
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("status", sa.String(10), nullable=False, index=True),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False, server_default=""),
        sa.Column("confidence", sa.Integer, nullable=False, server_default="0"),
        sa.Column("node_ids", sa.JSON, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("decided_at", TS),
        sa.Column("collection_id", sa.Uuid),
        sa.Column("outcome", sa.String(40), nullable=False, server_default=""),
    )


def downgrade() -> None:
    for table in ("collection_suggestion", "collection_share", "collection_member", "collection"):
        op.drop_table(table)
