"""Add revocable bearer tokens for native mobile clients.

Revision ID: 20261008_mobile_api_tokens
Revises: 20261007_hq_admin
"""
from alembic import op
import sqlalchemy as sa

revision = "20261008_mobile_api_tokens"
down_revision = "20261007_hq_admin"
branch_labels = None
depends_on = None


def upgrade():
    # app.py imports models.py, whose metadata.create_all() may create this new
    # table before Alembic runs. Make the migration safe in either startup order.
    bind = op.get_bind()
    if "mobile_api_tokens" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "mobile_api_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("device_name", sa.String(length=120), nullable=False, server_default="Android device"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_mobile_api_tokens_user_id", "mobile_api_tokens", ["user_id"])
    op.create_index("ix_mobile_api_tokens_expires_at", "mobile_api_tokens", ["expires_at"])
    op.create_index("ix_mobile_api_tokens_revoked_at", "mobile_api_tokens", ["revoked_at"])


def downgrade():
    op.drop_index("ix_mobile_api_tokens_revoked_at", table_name="mobile_api_tokens")
    op.drop_index("ix_mobile_api_tokens_expires_at", table_name="mobile_api_tokens")
    op.drop_index("ix_mobile_api_tokens_user_id", table_name="mobile_api_tokens")
    op.drop_table("mobile_api_tokens")
