"""Confidential PIN, session lock and re-authentication marker

Revision ID: 0015
Revises: 0014

M12b. `user_pin` holds the hashed PIN and its attempt counter; `auth_session` gains `locked_at`
(lock screen) and `reauth_at` (a fresh OIDC re-authentication, single use).
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps the tables.
    insp = sa.inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("auth_session")}
    if "locked_at" not in cols:
        op.add_column("auth_session", sa.Column("locked_at", sa.DateTime(timezone=True)))
    if "reauth_at" not in cols:
        op.add_column("auth_session", sa.Column("reauth_at", sa.DateTime(timezone=True)))
    if "reauth_strong" not in cols:
        op.add_column(
            "auth_session",
            sa.Column("reauth_strong", sa.Boolean, nullable=False, server_default=sa.false()),
        )
    if "user_pin" not in insp.get_table_names():
        op.create_table(
            "user_pin",
            sa.Column(
                "user_id",
                sa.Uuid,
                sa.ForeignKey("app_user.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("pin_hash", sa.String(512), nullable=False),
            sa.Column("set_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("failures", sa.Integer, nullable=False, server_default="0"),
            sa.Column("last_failure_at", sa.DateTime(timezone=True)),
            sa.Column("blocked_until", sa.DateTime(timezone=True)),
            sa.Column("disabled", sa.Boolean, nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    op.drop_table("user_pin")
    op.drop_column("auth_session", "reauth_strong")
    op.drop_column("auth_session", "reauth_at")
    op.drop_column("auth_session", "locked_at")
