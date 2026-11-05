"""configurable backup jobs (ADR-021, M27)

Revision ID: 0025
Revises: 0024

Adds `backup_job`, the per-job chain state, and `job_id`/`remote` on `backup_run`. The existing
global backup settings are the default job and stay where they are; their runs have no job_id.
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps the tables.
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "backup_job" not in tables:
        op.create_table(
            "backup_job",
            sa.Column("id", sa.Uuid, primary_key=True),
            sa.Column("name", sa.String(60), nullable=False, unique=True),
            sa.Column("enabled", sa.Boolean, nullable=False),
            sa.Column("scope", sa.String(10), nullable=False),
            sa.Column("encrypt", sa.Boolean, nullable=False),
            sa.Column("allow_confidential", sa.Boolean, nullable=False),
            sa.Column("backup_type", sa.String(8), nullable=False),
            sa.Column("interval_minutes", sa.Integer, nullable=False),
            sa.Column("chain_length", sa.Integer, nullable=False),
            sa.Column("min_chains_kept", sa.Integer, nullable=False),
            sa.Column("max_size_bytes", sa.BigInteger, nullable=True),
            sa.Column("destination_type", sa.String(8), nullable=False),
            sa.Column("destination", sa.JSON(), nullable=False),
            sa.Column("credentials_sealed", sa.LargeBinary, nullable=True),
            sa.Column("created_by", sa.Uuid, nullable=True),
            sa.Column("created_at", TS, nullable=False),
            sa.Column("updated_at", TS, nullable=False),
        )
    if "backup_job_chain_state" not in tables:
        op.create_table(
            "backup_job_chain_state",
            sa.Column("job_id", sa.Uuid, primary_key=True),
            sa.Column("root_id", sa.Uuid, sa.ForeignKey("storage_root.id"), primary_key=True),
            sa.Column("chain_id", sa.String(40), nullable=False),
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("snapshot", sa.LargeBinary, nullable=False),
            sa.Column("encrypted", sa.Boolean, nullable=False),
            sa.Column("key_fingerprint", sa.String(16), nullable=True),
            sa.Column("last_archive", sa.String(160), nullable=False),
            sa.Column("event_high_water", sa.BigInteger, nullable=False),
            sa.Column("updated_at", TS, nullable=False),
        )
    columns = {c["name"] for c in inspector.get_columns("backup_run")}
    if "job_id" not in columns:
        op.add_column("backup_run", sa.Column("job_id", sa.Uuid, nullable=True))
        op.create_index("ix_backup_run_job_id", "backup_run", ["job_id"])
    if "remote" not in columns:
        op.add_column(
            "backup_run",
            sa.Column("remote", sa.Boolean, nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    op.drop_column("backup_run", "remote")
    op.drop_index("ix_backup_run_job_id", table_name="backup_run")
    op.drop_column("backup_run", "job_id")
    op.drop_table("backup_job_chain_state")
    op.drop_table("backup_job")
