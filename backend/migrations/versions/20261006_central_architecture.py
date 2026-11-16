"""Central multi-site foundation.

Revision ID: 20261006_central
Revises: 89d4afaa53aa
"""
from alembic import op
import sqlalchemy as sa

revision = '20261006_central'
down_revision = '89d4afaa53aa'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'organizations' not in tables:
        op.create_table(
            'organizations',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('name', sa.String(length=200), nullable=False, unique=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        )

    if 'sites' not in tables:
        op.create_table(
            'sites',
            sa.Column('id', sa.String(length=36), primary_key=True),
            sa.Column('site_id', sa.String(length=20), nullable=False, unique=True),
            sa.Column('site_name', sa.String(length=200), nullable=False),
            sa.Column('organization_id', sa.String(length=36), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        )
        op.create_index('ix_sites_site_id', 'sites', ['site_id'], unique=True)
        op.create_index('ix_sites_organization_id', 'sites', ['organization_id'])

    if 'audit_number_counters' not in tables:
        op.create_table(
            'audit_number_counters',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('site_id', sa.String(length=20), nullable=False),
            sa.Column('year', sa.Integer(), nullable=False),
            sa.Column('last_sequence', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint('site_id', 'year', name='uq_audit_counter_site_year'),
        )


def downgrade():
    op.drop_table('audit_number_counters')
    op.drop_index('ix_sites_organization_id', table_name='sites')
    op.drop_index('ix_sites_site_id', table_name='sites')
    op.drop_table('sites')
    op.drop_table('organizations')
