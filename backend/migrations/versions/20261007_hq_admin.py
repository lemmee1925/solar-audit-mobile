"""Add global HQ administrator support.

Revision ID: 20261007_hq_admin
Revises: 20261006_central
"""
from alembic import op
import sqlalchemy as sa

revision = "20261007_hq_admin"
down_revision = "20261006_central"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column("site_id", existing_type=sa.String(length=20), nullable=True)
    else:
        op.alter_column("users", "site_id", existing_type=sa.String(length=20), nullable=True)


def downgrade():
    bind = op.get_bind()
    # A true downgrade is only safe if no HQ user has NULL site_id.
    null_count = bind.execute(sa.text("SELECT COUNT(*) FROM users WHERE site_id IS NULL")).scalar()
    if null_count:
        raise RuntimeError("Cannot downgrade HQ-admin migration while users with NULL site_id exist.")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column("site_id", existing_type=sa.String(length=20), nullable=False)
    else:
        op.alter_column("users", "site_id", existing_type=sa.String(length=20), nullable=False)
