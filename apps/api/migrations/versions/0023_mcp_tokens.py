"""personal access tokens for the optional MCP interface

Revision ID: 0023
Revises: 0022

M21. One table; nothing else changes. The interface itself stays off until the operator sets
FILECANOPY_MCP_ENABLED.
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps the tables.
    if "mcp_token" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "mcp_token",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid,
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("hint", sa.String(8), nullable=False),
        sa.Column("scopes", sa.JSON, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("last_used_at", TS, nullable=True),
        sa.Column("revoked_at", TS, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("mcp_token")
