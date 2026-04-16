"""adiciona data_pagamento_irrf em registros_rpv

Revision ID: 4a9b8c7d6e5f
Revises: b7d2f8c4a1e3
Create Date: 2026-03-24 10:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4a9b8c7d6e5f"
down_revision = "b7d2f8c4a1e3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("registros_rpv", schema=None) as batch_op:
        batch_op.add_column(sa.Column("data_pagamento_irrf", sa.Date(), nullable=True))


def downgrade():
    with op.batch_alter_table("registros_rpv", schema=None) as batch_op:
        batch_op.drop_column("data_pagamento_irrf")
