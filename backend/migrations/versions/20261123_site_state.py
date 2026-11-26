"""Add state column to sites table.

Revision ID: 20261123_site_state
Revises: 20261105_hq_org
"""
from alembic import op
import sqlalchemy as sa

revision = "20261123_site_state"
down_revision = "20261105_hq_org"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("sites")}
    if "state" not in columns:
        op.add_column("sites", sa.Column("state", sa.String(length=100), nullable=True))
        op.create_index("ix_sites_state", "sites", ["state"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("sites")}
    if "state" in columns:
        op.drop_index("ix_sites_state", table_name="sites")
        op.drop_column("sites", "state")
