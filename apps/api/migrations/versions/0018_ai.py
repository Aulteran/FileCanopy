"""AI providers, budgets and the usage ledger

Revision ID: 0018
Revises: 0017

M15. `ai_provider` holds configured providers (keys sealed), `ai_budget` per-month caps and
spending, `ai_usage` the ledger (no content). `app_user.ai_preference` defaults to "off".
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps the tables.
    bind = op.get_bind()
    names = set(sa.inspect(bind).get_table_names())
    if "ai_provider" not in names:
        op.create_table(
            "ai_provider",
            sa.Column("id", sa.Uuid, primary_key=True),
            sa.Column("scope", sa.String(8), nullable=False),
            sa.Column(
                "owner_user_id",
                sa.Uuid,
                sa.ForeignKey("app_user.id", ondelete="CASCADE"),
                index=True,
            ),
            sa.Column("kind", sa.String(24), nullable=False),
            sa.Column("privacy_mode", sa.String(8), nullable=False),
            sa.Column("label", sa.String(80), nullable=False, server_default=""),
            sa.Column("endpoint", sa.String(300), nullable=False, server_default=""),
            sa.Column("model", sa.String(120), nullable=False),
            sa.Column("price_in_micro", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("price_out_micro", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("secret_ciphertext", sa.LargeBinary),
            sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("consent_version", sa.Integer),
            sa.Column("consent_at", TS),
            sa.Column("consent_by", sa.Uuid),
            sa.Column("created_at", TS, nullable=False),
        )
    if "ai_budget" not in names:
        op.create_table(
            "ai_budget",
            sa.Column("id", sa.Uuid, primary_key=True),
            sa.Column("scope_key", sa.String(48), nullable=False),
            sa.Column("month", sa.String(7), nullable=False),
            sa.Column("cap_micro", sa.BigInteger, nullable=False),
            sa.Column("spent_micro", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("reserved_micro", sa.BigInteger, nullable=False, server_default="0"),
            sa.UniqueConstraint("scope_key", "month", name="uq_ai_budget_scope_month"),
        )
    if "ai_usage" not in names:
        op.create_table(
            "ai_usage",
            sa.Column("id", sa.Uuid, primary_key=True),
            sa.Column("at", TS, nullable=False, index=True),
            sa.Column("month", sa.String(7), nullable=False),
            sa.Column("scope_key", sa.String(48), nullable=False, index=True),
            sa.Column("actor_user_id", sa.Uuid),
            sa.Column("provider_id", sa.Uuid),
            sa.Column("provider_kind", sa.String(24), nullable=False),
            sa.Column("model", sa.String(120), nullable=False),
            sa.Column("purpose", sa.String(32), nullable=False),
            sa.Column("status", sa.String(10), nullable=False),
            sa.Column("reason", sa.String(40), nullable=False, server_default=""),
            sa.Column("input_tokens", sa.Integer),
            sa.Column("output_tokens", sa.Integer),
            sa.Column("reserved_micro", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("final_micro", sa.BigInteger, nullable=False, server_default="0"),
        )
    columns = {c["name"] for c in sa.inspect(bind).get_columns("app_user")}
    if "ai_preference" not in columns:
        with op.batch_alter_table("app_user") as batch:
            batch.add_column(
                sa.Column("ai_preference", sa.String(32), nullable=False, server_default="off")
            )


def downgrade() -> None:
    with op.batch_alter_table("app_user") as batch:
        batch.drop_column("ai_preference")
    op.drop_table("ai_usage")
    op.drop_table("ai_budget")
    op.drop_table("ai_provider")
