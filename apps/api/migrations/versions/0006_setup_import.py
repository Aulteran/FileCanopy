"""setup state, import batches and import entries

Revision ID: 0006
Revises: 0005

M06b (ADR-011 §1, §2, §10). Roots that already hold content start `closed`; empty roots `open`, so
existing users with empty personal folders still get their setup window.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "storage_root",
        sa.Column("setup_state", sa.String(8), nullable=False, server_default="open"),
    )
    op.execute(
        "UPDATE storage_root SET setup_state = 'closed' WHERE EXISTS "
        "(SELECT 1 FROM node WHERE node.root_id = storage_root.id AND node.parent_id IS NOT NULL)"
    )
    op.create_check_constraint(
        "ck_storage_root_setup_state", "storage_root", "setup_state in ('open','closed')"
    )

    op.create_table(
        "import_batch",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("root_id", sa.Uuid, sa.ForeignKey("storage_root.id"), nullable=False),
        sa.Column("target_node_id", sa.Uuid, nullable=False),
        sa.Column("actor_user_id", sa.Uuid, sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("include_system", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("plan", JSON, nullable=False),
        sa.Column("error_code", sa.String(48)),
        sa.Column("staged_zip", sa.String(64)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("finished_at", TS),
        sa.CheckConstraint(
            "kind in ('setup_folder','setup_zip','folder_upload')", name="ck_import_batch_kind"
        ),
        sa.CheckConstraint(
            "status in ('planned','running','done','stopped','failed','cancelled','expired')",
            name="ck_import_batch_status",
        ),
    )
    op.create_index("ix_import_batch_root_id", "import_batch", ["root_id"])
    op.create_index("ix_import_batch_actor_user_id", "import_batch", ["actor_user_id"])
    op.create_index("ix_import_batch_status", "import_batch", ["status"])

    op.create_table(
        "import_entry",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Uuid,
            sa.ForeignKey("import_batch.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer, nullable=False),
        sa.Column("parent_idx", sa.Integer),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("kind", sa.String(4), nullable=False),
        sa.Column("size", sa.BigInteger),
        sa.Column("mtime_ms", sa.BigInteger),
        sa.Column("src_index", sa.Integer),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("reason", sa.String(32)),
        sa.Column("node_id", sa.Uuid),
        sa.Column("result_name", sa.String(255)),
        sa.Column("dir_node_id", sa.Uuid),
        sa.Column("tmp_name", sa.String(64)),
        sa.Column("dst_name", sa.String(255)),
        sa.CheckConstraint("kind in ('dir','file')", name="ck_import_entry_kind"),
        sa.CheckConstraint(
            "status in ('pending','writing','done','renamed','skipped','failed')",
            name="ck_import_entry_status",
        ),
        sa.UniqueConstraint("batch_id", "idx", name="uq_import_entry_idx"),
    )
    op.create_index("ix_import_entry_batch_id", "import_entry", ["batch_id"])
    op.create_index("ix_import_entry_batch_status", "import_entry", ["batch_id", "status"])


def downgrade() -> None:
    op.drop_table("import_entry")
    op.drop_table("import_batch")
    op.drop_constraint("ck_storage_root_setup_state", "storage_root", type_="check")
    op.drop_column("storage_root", "setup_state")
