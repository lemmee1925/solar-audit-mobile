"""Add observations.finding_type using Alembic.

Revision ID: 20261104_finding_type
Revises: 20261008_mobile_api_tokens
"""
from alembic import op
import sqlalchemy as sa

revision = "20261104_finding_type"
down_revision = "20261008_mobile_api_tokens"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "observations" not in tables:
        raise RuntimeError(
            "Cannot add observations.finding_type: observations table is missing."
        )

    columns = {column["name"] for column in inspector.get_columns("observations")}
    if "finding_type" not in columns:
        op.add_column(
            "observations",
            sa.Column(
                "finding_type",
                sa.String(length=10),
                nullable=False,
                server_default=sa.text("'NC'"),
            ),
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "observations" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("observations")}
        if "finding_type" in columns:
            op.drop_column("observations", "finding_type")
