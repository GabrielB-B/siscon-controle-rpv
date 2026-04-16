"""adiciona numero se em rpvs e dativos

Revision ID: d1f8a4c6b2e0
Revises: 9e3b1a4d5c62
Create Date: 2026-04-06 11:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d1f8a4c6b2e0"
down_revision = "9e3b1a4d5c62"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("registros_rpv", schema=None) as batch_op:
        batch_op.add_column(sa.Column("numero_se", sa.String(length=50), nullable=True))

    with op.batch_alter_table("dativos_lotes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("numero_se", sa.String(length=50), nullable=True))

    with op.batch_alter_table("dativos_itens", schema=None) as batch_op:
        batch_op.add_column(sa.Column("numero_se", sa.String(length=50), nullable=True))


def downgrade():
    with op.batch_alter_table("dativos_itens", schema=None) as batch_op:
        batch_op.drop_column("numero_se")

    with op.batch_alter_table("dativos_lotes", schema=None) as batch_op:
        batch_op.drop_column("numero_se")

    with op.batch_alter_table("registros_rpv", schema=None) as batch_op:
        batch_op.drop_column("numero_se")
