"""Email ingestion: processing queue, filed attachments, counted notices

Revision ID: 0017
Revises: 0016

M14. `inbound_message` gains the processing state of a verified message (queue, retries, where it
was filed); `inbound_attachment` records the file it became or why it was not filed;
`notification` gains a count and a reason code (no names).
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def _missing(table: str, column: str) -> bool:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
    return column not in cols


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps the columns.
    adds: list[tuple[str, sa.Column]] = [  # type: ignore[type-arg]
        ("inbound_message", sa.Column("processing_state", sa.String(8))),
        (
            "inbound_message",
            sa.Column("process_attempts", sa.Integer, nullable=False, server_default="0"),
        ),
        ("inbound_message", sa.Column("process_after", TS)),
        ("inbound_message", sa.Column("process_error", sa.String(40))),
        (
            "inbound_message",
            sa.Column("filed_count", sa.Integer, nullable=False, server_default="0"),
        ),
        ("inbound_message", sa.Column("filed_node_id", sa.Uuid)),
        ("inbound_message", sa.Column("processed_at", TS)),
        (
            "inbound_message",
            sa.Column("manual_retries", sa.Integer, nullable=False, server_default="0"),
        ),
        ("inbound_attachment", sa.Column("node_id", sa.Uuid)),
        ("inbound_attachment", sa.Column("outcome", sa.String(24))),
        ("notification", sa.Column("count", sa.Integer)),
        ("notification", sa.Column("detail", sa.String(40))),
    ]
    for table, column in adds:
        if _missing(table, column.name):
            op.add_column(table, column)
    names = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("inbound_message")}
    if "ix_inbound_processing" not in names:
        op.create_index(
            "ix_inbound_processing", "inbound_message", ["processing_state", "process_after"]
        )


def downgrade() -> None:
    op.drop_index("ix_inbound_processing", table_name="inbound_message")
    for table, name in [
        ("notification", "detail"),
        ("notification", "count"),
        ("inbound_attachment", "outcome"),
        ("inbound_attachment", "node_id"),
        ("inbound_message", "manual_retries"),
        ("inbound_message", "processed_at"),
        ("inbound_message", "filed_node_id"),
        ("inbound_message", "filed_count"),
        ("inbound_message", "process_error"),
        ("inbound_message", "process_after"),
        ("inbound_message", "process_attempts"),
        ("inbound_message", "processing_state"),
    ]:
        op.drop_column(table, name)
