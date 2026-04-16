"""adiciona sem_irrf explicito em registros_rpv

Revision ID: b7d2f8c4a1e3
Revises: c2c65a4c9db3
Create Date: 2026-03-23 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b7d2f8c4a1e3"
down_revision = "c2c65a4c9db3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("registros_rpv", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("sem_irrf", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    with op.batch_alter_table("registros_rpv", schema=None) as batch_op:
        batch_op.alter_column("sem_irrf", server_default=None)


def downgrade():
    with op.batch_alter_table("registros_rpv", schema=None) as batch_op:
        batch_op.drop_column("sem_irrf")
