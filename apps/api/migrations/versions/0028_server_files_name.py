"""rename the shared tree's top folder from "Shared files" to "Server files"

Revision ID: 0028
Revises: 0027

"Shared files" now means what other people shared with you. The folder everyone on the server
uses is "Server files". Only the display name of the shared root's top node changes (the folder
on disk is unchanged), and only where it still has the old default name.
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def _rename(old: str, new: str) -> None:
    op.get_bind().execute(
        sa.text(
            "update node set name = :new, name_key = :key where parent_id is null and name = :old "
            "and root_id in (select id from storage_root where kind = 'shared')"
        ),
        {"new": new, "key": new.casefold(), "old": old},
    )


def upgrade() -> None:
    _rename("Shared files", "Server files")


def downgrade() -> None:
    _rename("Server files", "Shared files")
