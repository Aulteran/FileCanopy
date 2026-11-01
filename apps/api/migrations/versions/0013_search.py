"""search index and indexing jobs

Revision ID: 0013
Revises: 0012

M11. `search_doc` holds extracted text; on PostgreSQL a generated `tsvector` column and a GIN
index serve full-text queries (SQLite, used by unit tests and development, matches with LIKE).
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps the tables.
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    if "search_doc" not in tables:
        op.create_table(
            "search_doc",
            sa.Column(
                "node_id",
                sa.Uuid,
                sa.ForeignKey("node.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("root_id", sa.Uuid, nullable=False, index=True),
            sa.Column("revision", sa.Integer, nullable=False),
            sa.Column("mtime_ns", sa.BigInteger, nullable=False),
            sa.Column("size", sa.BigInteger, nullable=False),
            sa.Column("source", sa.String(8), nullable=False),
            sa.Column("body", sa.Text, nullable=False),
            sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "search_job" not in tables:
        op.create_table(
            "search_job",
            sa.Column(
                "node_id",
                sa.Uuid,
                sa.ForeignKey("node.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("revision", sa.Integer, nullable=False),
            sa.Column("mtime_ns", sa.BigInteger, nullable=False),
            sa.Column("size", sa.BigInteger, nullable=False),
            sa.Column("state", sa.String(8), nullable=False, index=True),
            sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
            sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error", sa.String(32), nullable=True),
        )
    if op.get_bind().dialect.name == "postgresql":
        cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("search_doc")}
        if "tsv" not in cols:
            op.execute(
                "ALTER TABLE search_doc ADD COLUMN tsv tsvector "
                "GENERATED ALWAYS AS (to_tsvector('simple', body)) STORED"
            )
        op.execute("CREATE INDEX IF NOT EXISTS ix_search_doc_tsv ON search_doc USING GIN (tsv)")


def downgrade() -> None:
    op.drop_table("search_job")
    op.drop_table("search_doc")
