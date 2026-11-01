"""privacy labels and confidential unlocks

Revision ID: 0011
Revises: 0010

M09. `node.privacy_label` (existing rows are normal) and `confidential_unlock`, one row per
(session, unlocked Confidential subtree); it vanishes with its session.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("node") as batch:
        batch.add_column(
            sa.Column(
                "privacy_label", sa.String(12), nullable=False, server_default="normal"
            )
        )
    op.create_table(
        "confidential_unlock",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "session_id",
            sa.Uuid,
            sa.ForeignKey("auth_session.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid, sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("scope_node_id", sa.Uuid, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_activity_at", TS, nullable=False),
        sa.UniqueConstraint("session_id", "scope_node_id", name="uq_unlock_scope"),
    )
    op.create_index("ix_confidential_unlock_session_id", "confidential_unlock", ["session_id"])
