"""optional backups: runs and chain state

Revision ID: 0008
Revises: 0007

M06c (ADR-015). Both tables hold counts, ids and sizes only. `backup_chain_state` keeps the file
fingerprints of the newest verified run so incremental runs need not read the archives (which are
encrypted with a key the app does not hold).
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "backup_run",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("root_id", sa.Uuid, sa.ForeignKey("storage_root.id"), nullable=False),
        sa.Column("chain_id", sa.String(40), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS),
        sa.Column("archive_name", sa.String(160)),
        sa.Column("size_bytes", sa.BigInteger),
        sa.Column("uncompressed_bytes", sa.BigInteger),
        sa.Column("archive_sha256", sa.String(64)),
        sa.Column("files_added", sa.Integer, nullable=False, server_default="0"),
        sa.Column("files_changed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("files_deleted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("files_missing", sa.Integer, nullable=False, server_default="0"),
        sa.Column("files_unstable", sa.Integer, nullable=False, server_default="0"),
        sa.Column("encrypted", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("key_fingerprint", sa.String(16)),
        sa.Column("error_code", sa.String(48)),
        sa.Column("event_high_water", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("pruned_at", TS),
        sa.CheckConstraint("kind in ('full','incremental')", name="ck_backup_run_kind"),
        sa.CheckConstraint(
            "status in ('running','ok','failed','refused')", name="ck_backup_run_status"
        ),
    )
    op.create_index("ix_backup_run_root_id", "backup_run", ["root_id"])
    op.create_index("ix_backup_run_root_started", "backup_run", ["root_id", "started_at"])
    op.create_table(
        "backup_chain_state",
        sa.Column("root_id", sa.Uuid, sa.ForeignKey("storage_root.id"), primary_key=True),
        sa.Column("chain_id", sa.String(40), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("snapshot", sa.LargeBinary, nullable=False),
        sa.Column("encrypted", sa.Boolean, nullable=False),
        sa.Column("key_fingerprint", sa.String(16)),
        sa.Column("last_archive", sa.String(160), nullable=False),
        sa.Column("event_high_water", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("updated_at", TS, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("backup_chain_state")
    op.drop_index("ix_backup_run_root_started", table_name="backup_run")
    op.drop_index("ix_backup_run_root_id", table_name="backup_run")
    op.drop_table("backup_run")
