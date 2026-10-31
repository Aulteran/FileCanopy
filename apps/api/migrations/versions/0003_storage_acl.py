"""storage roots, nodes, folder grants; backfill roots for existing accounts

Revision ID: 0003
Revises: 0002

Rows only: the migrate container has no library mount, so directories are created by the API
at startup (`storage.roots.ensure_roots`).
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
DEFAULT_ACCESS_KEY = "shared_tree_default_access"


def upgrade() -> None:
    op.create_table(
        "storage_root",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("owner_user_id", sa.Uuid, sa.ForeignKey("app_user.id"), unique=True),
        sa.Column("relative_dir", sa.String(300), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint("kind in ('shared','personal')", name="ck_storage_root_kind"),
        sa.CheckConstraint(
            "status in ('active','missing','disabled')", name="ck_storage_root_status"
        ),
        sa.CheckConstraint(
            "(kind = 'personal') = (owner_user_id is not null)", name="ck_storage_root_owner"
        ),
    )
    op.create_index(
        "uq_storage_root_one_shared",
        "storage_root",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("kind = 'shared'"),
    )
    op.create_table(
        "node",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("root_id", sa.Uuid, sa.ForeignKey("storage_root.id"), nullable=False),
        sa.Column("parent_id", sa.Uuid, sa.ForeignKey("node.id")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("lineage", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint("kind in ('dir','file')", name="ck_node_kind"),
        sa.UniqueConstraint("parent_id", "name", name="uq_node_parent_name"),
    )
    op.create_index("ix_node_root_id", "node", ["root_id"])
    op.create_index("ix_node_parent_id", "node", ["parent_id"])
    op.create_index(
        "uq_node_one_root",
        "node",
        ["root_id"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.create_index(
        "ix_node_lineage", "node", ["lineage"], postgresql_ops={"lineage": "text_pattern_ops"}
    )
    op.create_table(
        "node_grant",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("node_id", sa.Uuid, sa.ForeignKey("node.id", ondelete="CASCADE"), nullable=False),
        sa.Column("grantee_user_id", sa.Uuid, sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("granted_by", sa.Uuid, sa.ForeignKey("app_user.id")),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint("level in ('viewer','editor')", name="ck_node_grant_level"),
        sa.UniqueConstraint("node_id", "grantee_user_id", name="uq_node_grant"),
    )
    op.create_index("ix_node_grant_node_id", "node_grant", ["node_id"])
    op.create_index("ix_node_grant_grantee_user_id", "node_grant", ["grantee_user_id"])

    _backfill()


def _backfill() -> None:
    conn = op.get_bind()
    now = datetime.now(UTC)
    root_t = sa.table(
        "storage_root",
        sa.column("id", sa.Uuid),
        sa.column("kind", sa.String),
        sa.column("owner_user_id", sa.Uuid),
        sa.column("relative_dir", sa.String),
        sa.column("status", sa.String),
        sa.column("created_at", TS),
    )
    node_t = sa.table(
        "node",
        sa.column("id", sa.Uuid),
        sa.column("root_id", sa.Uuid),
        sa.column("parent_id", sa.Uuid),
        sa.column("name", sa.String),
        sa.column("kind", sa.String),
        sa.column("lineage", sa.Text),
        sa.column("status", sa.String),
        sa.column("created_at", TS),
        sa.column("updated_at", TS),
    )
    grant_t = sa.table(
        "node_grant",
        sa.column("id", sa.Uuid),
        sa.column("node_id", sa.Uuid),
        sa.column("grantee_user_id", sa.Uuid),
        sa.column("level", sa.String),
        sa.column("granted_by", sa.Uuid),
        sa.column("created_at", TS),
    )
    conn.execute(
        sa.text(
            "INSERT INTO app_setting (key, value) VALUES (:k, to_jsonb(CAST('editor' AS text))) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {"k": DEFAULT_ACCESS_KEY},
    )

    def add_root(
        kind: str, owner: uuid.UUID | None, relative_dir: str, node_name: str
    ) -> uuid.UUID:
        root_id, node_id = uuid.uuid4(), uuid.uuid4()
        conn.execute(
            sa.insert(root_t).values(
                id=root_id,
                kind=kind,
                owner_user_id=owner,
                relative_dir=relative_dir,
                status="active",
                created_at=now,
            )
        )
        conn.execute(
            sa.insert(node_t).values(
                id=node_id,
                root_id=root_id,
                parent_id=None,
                name=node_name,
                kind="dir",
                lineage=f"/{node_id}/",
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        return node_id

    shared_node = add_root("shared", None, "shared", "Shared files")
    users = conn.execute(sa.text("SELECT id, status FROM app_user")).all()
    for user_id, status in users:
        add_root("personal", user_id, f"personal/{user_id}", "My files")
        if status != "disabled":  # the instance default is 'editor' for existing accounts
            conn.execute(
                sa.insert(grant_t).values(
                    id=uuid.uuid4(),
                    node_id=shared_node,
                    grantee_user_id=user_id,
                    level="editor",
                    granted_by=None,
                    created_at=now,
                )
            )


def downgrade() -> None:
    for table in ("node_grant", "node", "storage_root"):
        op.drop_table(table)
    op.execute("DELETE FROM app_setting WHERE key = 'shared_tree_default_access'")
