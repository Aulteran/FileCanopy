"""optional local semantic search

Revision ID: 0022
Revises: 0021

M19. `search_chunk` holds one vector per chunk of extracted text, `search_embed` the per-file
state and job. On PostgreSQL the vector column is pgvector's `vector`. The extension is optional:
when it cannot be created (an external database without pgvector) the column is plain text, the
API reports `pgvector_missing` and semantic search simply stays off; nothing else changes.
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def _try_vector_extension() -> bool:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return False
    try:
        with bind.begin_nested():  # a savepoint: a failure must not abort the migration
            bind.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        return True
    except Exception:
        return False


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps the tables.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "semantic_opt_out" not in {c["name"] for c in insp.get_columns("app_user")}:
        op.add_column(
            "app_user",
            sa.Column("semantic_opt_out", sa.Boolean, nullable=False, server_default=sa.false()),
        )
    have = set(insp.get_table_names())
    if "search_embed" not in have:
        op.create_table(
            "search_embed",
            sa.Column(
                "node_id",
                sa.Uuid,
                sa.ForeignKey("node.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("generation", sa.Integer, nullable=False),
            sa.Column("revision", sa.Integer, nullable=False),
            sa.Column("mtime_ns", sa.BigInteger, nullable=False),
            sa.Column("size", sa.BigInteger, nullable=False),
            sa.Column("state", sa.String(8), nullable=False, index=True),
            sa.Column("chunks", sa.Integer, nullable=False, server_default="0"),
            sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
            sa.Column("run_after", TS, nullable=False),
            sa.Column("lease_until", TS, nullable=True),
            sa.Column("error", sa.String(32), nullable=True),
        )
    if "search_chunk" in have:
        return
    vector = _try_vector_extension()
    id_type = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        "search_chunk",
        sa.Column("id", id_type, primary_key=True, autoincrement=True),
        sa.Column(
            "node_id",
            sa.Uuid,
            sa.ForeignKey("node.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("root_id", sa.Uuid, nullable=False, index=True),
        sa.Column("generation", sa.Integer, nullable=False, index=True),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("start", sa.Integer, nullable=False),
        sa.Column("end", sa.Integer, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("mtime_ns", sa.BigInteger, nullable=False),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("embedding", sa.Text, nullable=True),
        sa.UniqueConstraint("node_id", "ordinal", name="uq_search_chunk_node_ord"),
    )
    if vector:
        op.execute("ALTER TABLE search_chunk ALTER COLUMN embedding TYPE vector USING NULL")


def downgrade() -> None:
    op.drop_table("search_chunk")
    op.drop_table("search_embed")
    op.drop_column("app_user", "semantic_opt_out")
