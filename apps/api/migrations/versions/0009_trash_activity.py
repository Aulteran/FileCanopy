"""trash, activity history and undo

Revision ID: 0009
Revises: 0008

M07a (ADR-017). `trash_item` holds the metadata of trashed entries (no host paths: the on-disk
location is derived from ids). Nodes gain the `trashed` and `purged` statuses, which release their
name like `dismissed`, plus the id of the item that holds them. `file_event` gains a correlation id
(the `file_operation` or import batch it belongs to), the user an AI agent acted for, an index for
per-node history and the `ai_agent` actor kind. Existing events are kept unchanged.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
OLD_WHERE = "status NOT IN ('dismissed','missing')"
NEW_WHERE = "status NOT IN ('dismissed','missing','trashed','purged')"


def upgrade() -> None:
    op.drop_constraint("ck_node_status", "node", type_="check")
    op.create_check_constraint(
        "ck_node_status",
        "node",
        "status in ('active','busy','reconcile_needed','missing','dismissed','trashed','purged')",
    )
    op.add_column("node", sa.Column("trash_item_id", sa.Uuid))
    op.create_index("ix_node_trash_item_id", "node", ["trash_item_id"])
    op.drop_index("uq_node_parent_name", "node")
    op.create_index(
        "uq_node_parent_name",
        "node",
        ["parent_id", "name"],
        unique=True,
        postgresql_where=sa.text(NEW_WHERE),
    )

    op.add_column("file_event", sa.Column("correlation_id", sa.Uuid))
    op.add_column("file_event", sa.Column("on_behalf_of_user_id", sa.Uuid))
    op.create_index("ix_file_event_correlation_id", "file_event", ["correlation_id"])
    op.create_index("ix_file_event_node_at", "file_event", ["node_id", "at"])
    op.drop_constraint("ck_file_event_actor", "file_event", type_="check")
    op.create_check_constraint(
        "ck_file_event_actor",
        "file_event",
        "actor_kind in ('user','external_filesystem','system','ai_agent')",
    )

    op.create_table(
        "trash_item",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("root_id", sa.Uuid, sa.ForeignKey("storage_root.id"), nullable=False),
        sa.Column("node_id", sa.Uuid, nullable=False),
        sa.Column("original_parent_id", sa.Uuid, nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("entry_count", sa.Integer, nullable=False),
        sa.Column("trashed_by", sa.Uuid, sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("trashed_at", TS, nullable=False),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("purge_after", TS),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("finished_at", TS),
        sa.CheckConstraint(
            "reason in ('deleted','moved_out','import_undone','copy_undone')",
            name="ck_trash_item_reason",
        ),
        sa.CheckConstraint(
            "status in ('in_trash','busy','restored','purged','reconcile_needed')",
            name="ck_trash_item_status",
        ),
    )
    op.create_index("ix_trash_item_node_id", "trash_item", ["node_id"])
    op.create_index("ix_trash_item_root_status", "trash_item", ["root_id", "status"])


def downgrade() -> None:
    op.drop_table("trash_item")
    op.drop_constraint("ck_file_event_actor", "file_event", type_="check")
    op.execute("DELETE FROM file_event WHERE actor_kind = 'ai_agent'")
    op.create_check_constraint(
        "ck_file_event_actor", "file_event", "actor_kind in ('user','external_filesystem','system')"
    )
    op.drop_index("ix_file_event_node_at", "file_event")
    op.drop_index("ix_file_event_correlation_id", "file_event")
    op.drop_column("file_event", "on_behalf_of_user_id")
    op.drop_column("file_event", "correlation_id")
    # Trashed and purged nodes cannot exist under the older status set: they become dismissed.
    op.execute("UPDATE node SET status = 'dismissed' WHERE status IN ('trashed','purged')")
    op.drop_index("uq_node_parent_name", "node")
    op.create_index(
        "uq_node_parent_name",
        "node",
        ["parent_id", "name"],
        unique=True,
        postgresql_where=sa.text(OLD_WHERE),
    )
    op.drop_index("ix_node_trash_item_id", "node")
    op.drop_column("node", "trash_item_id")
    op.drop_constraint("ck_node_status", "node", type_="check")
    op.create_check_constraint(
        "ck_node_status",
        "node",
        "status in ('active','busy','reconcile_needed','missing','dismissed')",
    )
