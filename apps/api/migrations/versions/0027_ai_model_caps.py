"""a monthly spending limit for each server AI model (replaces the server-wide limit)

Revision ID: 0027
Revises: 0026

`ai_provider.monthly_cap_micro` is the model's own monthly limit in micro-dollars; NULL means the
administrator cleared it (no limit). Spending limits are per model from now on and the
server-wide limit is no longer used for server models. So that no install ends up unlimited, every
existing cloud server model gets the old server-wide limit (the newest `ai_budget` row for scope
`instance`, else $3). People's own-provider limits (`user:<id>` scopes) are untouched.
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

DEFAULT_CAP = 3_000_000


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps the tables.
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("ai_provider")}
    if "monthly_cap_micro" in columns:
        return
    op.add_column("ai_provider", sa.Column("monthly_cap_micro", sa.BigInteger(), nullable=True))
    old = bind.execute(
        sa.text(
            "select cap_micro from ai_budget where scope_key = 'instance' "
            "order by month desc limit 1"
        )
    ).scalar()
    bind.execute(
        sa.text(
            "update ai_provider set monthly_cap_micro = :cap "
            "where scope = 'instance' and privacy_mode = 'cloud'"
        ),
        {"cap": DEFAULT_CAP if old is None else old},
    )


def downgrade() -> None:
    with op.batch_alter_table("ai_provider") as batch:
        batch.drop_column("monthly_cap_micro")
