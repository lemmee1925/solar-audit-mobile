"""Add ups_type column to plants and audit_site_details tables.

Revision ID: 20261126_ups_type
Revises: 20261124_state_admin
"""
from alembic import op
import sqlalchemy as sa

revision = "20261126_ups_type"
down_revision = "20261124_state_admin"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for tbl in ("plants", "audit_site_details"):
        cols = {c["name"] for c in inspector.get_columns(tbl)}
        if "ups_type" not in cols:
            op.add_column(tbl, sa.Column("ups_type", sa.String(length=200), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for tbl in ("plants", "audit_site_details"):
        cols = {c["name"] for c in inspector.get_columns(tbl)}
        if "ups_type" in cols:
            op.drop_column(tbl, "ups_type")
