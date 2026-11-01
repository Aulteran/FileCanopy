"""local-processing opt-in for previews of Private and Confidential files

Revision ID: 0012
Revises: 0011

M10. One column on `app_user`; existing accounts are opted out. Derivatives themselves live in
`app-data/previews/`, not in the database.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps `app_user`.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("app_user")}
    if "local_processing_opt_in" in columns:
        return
    with op.batch_alter_table("app_user") as batch:
        batch.add_column(
            sa.Column(
                "local_processing_opt_in", sa.Boolean, nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("app_user") as batch:
        batch.drop_column("local_processing_opt_in")
