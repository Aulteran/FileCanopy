"""linked sign-in identities and pending OIDC logins

Revision ID: 0014
Revises: 0013

M12. `user_identity` links an account to (issuer, subject); `oidc_login` holds one pending
round trip (state hash, nonce, PKCE verifier, browser binding), single use and short lived.
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: the Compose migration test rewinds the version number but keeps the tables.
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "user_identity" not in tables:
        op.create_table(
            "user_identity",
            sa.Column("id", sa.Uuid, primary_key=True),
            sa.Column(
                "user_id",
                sa.Uuid,
                sa.ForeignKey("app_user.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("issuer", sa.String(512), nullable=False),
            sa.Column("subject", sa.String(255), nullable=False),
            sa.Column("email_seen", sa.String(254), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("issuer", "subject", name="uq_identity_issuer_subject"),
            sa.UniqueConstraint("user_id", "issuer", name="uq_identity_user_issuer"),
        )
    if "oidc_login" not in tables:
        op.create_table(
            "oidc_login",
            sa.Column("id", sa.Uuid, primary_key=True),
            sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("binding_hash", sa.String(64), nullable=False),
            sa.Column("nonce", sa.String(64), nullable=False),
            sa.Column("code_verifier", sa.String(128), nullable=False),
            sa.Column("purpose", sa.String(8), nullable=False),
            sa.Column("user_id", sa.Uuid, nullable=True),
            sa.Column("session_id", sa.Uuid, nullable=True),
            sa.Column("token_hash", sa.String(64), nullable=True),
            sa.Column("password_hash", sa.String(512), nullable=True),
            sa.Column("new_name", sa.String(200), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("oidc_login")
    op.drop_table("user_identity")
