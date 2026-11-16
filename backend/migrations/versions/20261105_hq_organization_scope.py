"""Bind HQ administrator identities to one organization.

Revision ID: 20261105_hq_org
Revises: 20261008_mobile_api_tokens
"""
from alembic import op
import sqlalchemy as sa

revision = "20261105_hq_org"
down_revision = "20261104_finding_type"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # Normalize the two legacy role labels to the approved four-role model.
    # This is intentionally one-way: the old labels are ambiguous after
    # standardization and must not be recreated by a downgrade.
    bind.execute(sa.text("UPDATE users SET role = 'site_admin' WHERE lower(role) = 'admin'"))
    bind.execute(sa.text("UPDATE users SET role = 'user' WHERE lower(role) = 'technician'"))
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "organization_id" not in columns:
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("users", recreate="always") as batch_op:
                batch_op.add_column(sa.Column("organization_id", sa.String(length=36), nullable=True))
                batch_op.create_foreign_key(
                    "fk_users_organization_id_organizations",
                    "organizations", ["organization_id"], ["id"],
                )
        else:
            op.add_column("users", sa.Column("organization_id", sa.String(length=36), nullable=True))
            op.create_foreign_key(
                "fk_users_organization_id_organizations",
                "users", "organizations", ["organization_id"], ["id"],
            )
        op.create_index("ix_users_organization_id", "users", ["organization_id"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "organization_id" in columns:
        op.drop_index("ix_users_organization_id", table_name="users")
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("users", recreate="always") as batch_op:
                batch_op.drop_constraint("fk_users_organization_id_organizations", type_="foreignkey")
                batch_op.drop_column("organization_id")
        else:
            op.drop_constraint("fk_users_organization_id_organizations", "users", type_="foreignkey")
            op.drop_column("users", "organization_id")
