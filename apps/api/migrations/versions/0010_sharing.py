"""sharing notifications

Revision ID: 0010
Revises: 0009

M08. `notification` holds in-app notices (ids and a kind only; text is built at read time).
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "notification",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "recipient_user_id",
            sa.Uuid,
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("node_id", sa.Uuid),
        sa.Column("actor_user_id", sa.Uuid),
        sa.Column("level", sa.String(8)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("read_at", TS),
    )
    op.create_index(
        "ix_notification_recipient_at", "notification", ["recipient_user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("notification")
