"""one open setup import per root, scan-pause tracking and import report retention

Revision ID: 0007
Revises: 0006

M06b security review. A partial unique index makes "one unfinished setup import per root" a
database guarantee (an older duplicate, if any, is cancelled first); `scan_paused_since` bounds how
long an import may hold the reconciler off a root; `entries_pruned_at` marks batches whose report
rows were deleted by the retention job.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
OPEN_WHERE = "kind in ('setup_folder','setup_zip') and status in ('planned','running','stopped')"


def upgrade() -> None:
    op.add_column("storage_root", sa.Column("scan_paused_since", TS))
    op.add_column("import_batch", sa.Column("entries_pruned_at", TS))
    # Keep the newest unfinished setup batch of each root; cancel any older ones.
    op.execute(
        "UPDATE import_batch SET status = 'cancelled' "
        "WHERE kind in ('setup_folder','setup_zip') "
        "AND status in ('planned','running','stopped') AND EXISTS ("
        "SELECT 1 FROM import_batch newer WHERE newer.root_id = import_batch.root_id "
        "AND newer.kind in ('setup_folder','setup_zip') "
        "AND newer.status in ('planned','running','stopped') "
        "AND (newer.created_at > import_batch.created_at "
        "OR (newer.created_at = import_batch.created_at AND newer.id > import_batch.id)))"
    )
    op.create_index(
        "uq_import_batch_open_setup",
        "import_batch",
        ["root_id"],
        unique=True,
        sqlite_where=sa.text(OPEN_WHERE),
        postgresql_where=sa.text(OPEN_WHERE),
    )


def downgrade() -> None:
    op.drop_index("uq_import_batch_open_setup", table_name="import_batch")
    op.drop_column("import_batch", "entries_pruned_at")
    op.drop_column("storage_root", "scan_paused_since")
