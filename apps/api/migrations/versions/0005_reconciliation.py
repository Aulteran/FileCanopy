"""filesystem identity, external-change reconciliation state, scan runs and file events

Revision ID: 0005
Revises: 0004

M06a (ADR-011). Setup state and import tables belong to M06b and are not created here.
"""

import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("node", sa.Column("fs_dev", sa.BigInteger))
    op.add_column("node", sa.Column("fs_ino", sa.BigInteger))
    op.add_column("node", sa.Column("mtime_ns", sa.BigInteger))
    op.add_column("node", sa.Column("content_hash", sa.String(64)))
    op.add_column("node", sa.Column("name_key", sa.Text))
    op.add_column("node", sa.Column("unsupported_reason", sa.String(24)))
    op.add_column("node", sa.Column("raw_name_digest", sa.String(64)))
    op.add_column("node", sa.Column("last_seen_run", sa.Uuid))

    conn = op.get_bind()
    for node_id, name in conn.execute(sa.text("SELECT id, name FROM node")).all():
        key = unicodedata.normalize("NFC", name).casefold()
        conn.execute(
            sa.text("UPDATE node SET name_key = :k WHERE id = :i"), {"k": key, "i": node_id}
        )
    op.alter_column("node", "name_key", nullable=False)
    op.create_index("ix_node_parent_name_key", "node", ["parent_id", "name_key"])

    # Dismissed and missing nodes stay for audit/recovery but release their name.
    op.drop_constraint("uq_node_parent_name", "node", type_="unique")
    op.create_index(
        "uq_node_parent_name",
        "node",
        ["parent_id", "name"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('dismissed','missing')"),
    )
    op.drop_constraint("ck_node_kind", "node", type_="check")
    op.create_check_constraint("ck_node_kind", "node", "kind in ('dir','file','other')")
    op.create_check_constraint(
        "ck_node_status",
        "node",
        "status in ('active','busy','reconcile_needed','missing','dismissed')",
    )

    # "unsupported_mount" is 17 characters; 0003 created the column as varchar(16).
    op.alter_column("storage_root", "status", type_=sa.String(24), existing_nullable=False)
    op.add_column("storage_root", sa.Column("scan_state", sa.String(16)))
    op.add_column("storage_root", sa.Column("fs_type", sa.String(32)))
    op.add_column(
        "storage_root",
        sa.Column("network_fs", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.drop_constraint("ck_storage_root_status", "storage_root", type_="check")
    op.create_check_constraint(
        "ck_storage_root_status",
        "storage_root",
        "status in ('active','missing','disabled','unsupported_mount')",
    )

    op.create_table(
        "scan_run",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("root_id", sa.Uuid, sa.ForeignKey("storage_root.id"), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(48)),
        sa.Column("counts", JSON),
    )
    op.create_index("ix_scan_run_root_id", "scan_run", ["root_id"])
    op.create_table(
        "file_event",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("node_id", sa.Uuid, nullable=False),
        sa.Column("at", TS, nullable=False),
        sa.Column("actor_kind", sa.String(24), nullable=False),
        sa.Column("actor_user_id", sa.Uuid),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("details", JSON, nullable=False),
        sa.CheckConstraint(
            "actor_kind in ('user','external_filesystem','system')", name="ck_file_event_actor"
        ),
    )
    op.create_index("ix_file_event_node_id", "file_event", ["node_id"])
    op.create_index("ix_file_event_at", "file_event", ["at"])


def downgrade() -> None:
    op.drop_table("file_event")
    op.drop_table("scan_run")
    op.execute("UPDATE storage_root SET status = 'missing' WHERE status = 'unsupported_mount'")
    op.drop_constraint("ck_storage_root_status", "storage_root", type_="check")
    op.create_check_constraint(
        "ck_storage_root_status", "storage_root", "status in ('active','missing','disabled')"
    )
    op.alter_column("storage_root", "status", type_=sa.String(16), existing_nullable=False)
    for col in ("network_fs", "fs_type", "scan_state"):
        op.drop_column("storage_root", col)
    op.drop_constraint("ck_node_status", "node", type_="check")
    op.drop_constraint("ck_node_kind", "node", type_="check")
    op.create_check_constraint("ck_node_kind", "node", "kind in ('dir','file')")
    op.drop_index("uq_node_parent_name", "node")
    op.create_unique_constraint("uq_node_parent_name", "node", ["parent_id", "name"])
    op.drop_index("ix_node_parent_name_key", "node")
    for col in (
        "last_seen_run",
        "raw_name_digest",
        "unsupported_reason",
        "name_key",
        "content_hash",
        "mtime_ns",
        "fs_ino",
        "fs_dev",
    ):
        op.drop_column("node", col)
