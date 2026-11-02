"""AI organization proposals

Revision ID: 0019
Revises: 0018

M16. `ai_proposal` holds one open proposal per file (partial unique index); `app_user.ai_organize`
defaults to "off".
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps the tables.
    bind = op.get_bind()
    names = set(sa.inspect(bind).get_table_names())
    if "ai_proposal" not in names:
        op.create_table(
            "ai_proposal",
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
            sa.Column("kind", sa.String(12), nullable=False),
            sa.Column("new_name", sa.String(255)),
            sa.Column("target_parent_id", sa.Uuid),
            sa.Column("new_folder_name", sa.String(255)),
            sa.Column("doc_type", sa.String(40), nullable=False, server_default=""),
            sa.Column("reason", sa.String(200), nullable=False, server_default=""),
            sa.Column("confidence", sa.Integer, nullable=False, server_default="0"),
            sa.Column("sensitive", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("sensitive_reasons", sa.JSON, nullable=False),
            sa.Column("auto_blockers", sa.JSON, nullable=False),
            sa.Column("snapshot", sa.JSON, nullable=False),
            sa.Column("created_at", TS, nullable=False),
            sa.Column("decided_at", TS),
            sa.Column("decided_by", sa.Uuid),
            sa.Column("auto", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("operation_id", sa.Uuid),
            sa.Column("outcome", sa.String(40), nullable=False, server_default=""),
        )
        open_only = sa.text("status IN ('pending','applying')")
        op.create_index(
            "uq_ai_proposal_open",
            "ai_proposal",
            ["node_id"],
            unique=True,
            sqlite_where=open_only,
            postgresql_where=open_only,
        )
    columns = {c["name"] for c in sa.inspect(bind).get_columns("app_user")}
    if "ai_organize" not in columns:
        with op.batch_alter_table("app_user") as batch:
            batch.add_column(
                sa.Column("ai_organize", sa.String(8), nullable=False, server_default="off")
            )


def downgrade() -> None:
    with op.batch_alter_table("app_user") as batch:
        batch.drop_column("ai_organize")
    op.drop_table("ai_proposal")
