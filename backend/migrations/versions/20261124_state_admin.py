"""Add assigned_state column to users table for state_admin role.

Revision ID: 20261124_state_admin
Revises: 20261123_site_state
"""
from alembic import op
import sqlalchemy as sa

revision = "20261124_state_admin"
down_revision = "20261123_site_state"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "assigned_state" not in columns:
        op.add_column("users", sa.Column("assigned_state", sa.String(length=100), nullable=True))
        op.create_index("ix_users_assigned_state", "users", ["assigned_state"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "assigned_state" in columns:
        op.drop_index("ix_users_assigned_state", table_name="users")
        op.drop_column("users", "assigned_state")
