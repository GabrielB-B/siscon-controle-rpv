"""adiciona reinf_status em dativos

Revision ID: c2c65a4c9db3
Revises: ef6d5efc735e
Create Date: 2026-03-20 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2c65a4c9db3'
down_revision = 'ef6d5efc735e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('dativos_lotes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reinf_status', sa.String(length=30), nullable=True))

    with op.batch_alter_table('dativos_itens', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reinf_status', sa.String(length=30), nullable=True))


def downgrade():
    with op.batch_alter_table('dativos_itens', schema=None) as batch_op:
        batch_op.drop_column('reinf_status')

    with op.batch_alter_table('dativos_lotes', schema=None) as batch_op:
        batch_op.drop_column('reinf_status')
