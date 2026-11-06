"""several server AI models and a server-wide AI switch

Revision ID: 0026
Revises: 0025

The server may now hold several AI models, one of them the default (the existing
`ai.instance_provider_id` setting), so the one-instance-model unique index goes. Installs that
already have a server model keep working: the new `ai.enabled` setting is written as true for
them (and for installs with only a personal provider, which was usable before), false
otherwise. People's stored AI choices (`app_user.ai_preference`) keep their values and meaning.

Downgrade keeps only the oldest server model (the old schema allows one) and does NOT switch AI
off: the old code simply uses that model if it is enabled.
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps the tables.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "uq_ai_provider_instance" in {i["name"] for i in inspector.get_indexes("ai_provider")}:
        op.drop_index("uq_ai_provider_instance", table_name="ai_provider")
    has_any = bind.execute(sa.text("select 1 from ai_provider limit 1")).first() is not None
    has_flag = (
        bind.execute(sa.text("select 1 from app_setting where key = 'ai.enabled'")).first()
        is not None
    )
    if not has_flag:
        setting = sa.table("app_setting", sa.column("key", sa.String), sa.column("value", sa.JSON))
        op.bulk_insert(setting, [{"key": "ai.enabled", "value": has_any}])


def downgrade() -> None:
    # Keep the oldest server model so the one-model index can be rebuilt.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "delete from ai_provider where scope = 'instance' and id not in "
            "(select id from ai_provider where scope = 'instance' order by created_at limit 1)"
        )
    )
    op.create_index(
        "uq_ai_provider_instance",
        "ai_provider",
        ["scope"],
        unique=True,
        sqlite_where=sa.text("scope = 'instance'"),
        postgresql_where=sa.text("scope = 'instance'"),
    )
