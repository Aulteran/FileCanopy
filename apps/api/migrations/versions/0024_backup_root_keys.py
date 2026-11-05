"""per-root backup keys (ADR-016, M25)

Revision ID: 0024
Revises: 0023

One table. Existing archives and the instance key are untouched: chains written under the instance
key stay readable with it and are reported as legacy. Personal roots start per-root key chains only
after their owner has created and confirmed a key.
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps the tables.
    if "backup_root_key" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "backup_root_key",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("root_id", sa.Uuid, sa.ForeignKey("storage_root.id"), nullable=False, index=True),
        sa.Column("public", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(16), nullable=False),
        sa.Column("state", sa.String(8), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("confirmed_at", TS, nullable=True),
        sa.Column("retired_at", TS, nullable=True),
        sa.Column("escrow_blob", sa.LargeBinary, nullable=True),
        sa.Column("escrow_key_fingerprint", sa.String(16), nullable=True),
        sa.Column("escrow_changed_at", TS, nullable=True),
    )
    for name, state in (
        ("uq_backup_root_key_one_active", "active"),
        ("uq_backup_root_key_one_pending", "pending"),
    ):
        op.create_index(
            name,
            "backup_root_key",
            ["root_id"],
            unique=True,
            postgresql_where=sa.text(f"state = '{state}'"),
            sqlite_where=sa.text(f"state = '{state}'"),
        )


def downgrade() -> None:
    op.drop_table("backup_root_key")
