"""adiciona confirmacao de dispensa de irrf em dativos_itens

Revision ID: 3e4a1d2c9b80
Revises: 9d3e6f4a1b20
Create Date: 2026-03-31 16:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "3e4a1d2c9b80"
down_revision = "9d3e6f4a1b20"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("dativos_itens", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "dispensa_irrf_confirmada",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade():
    with op.batch_alter_table("dativos_itens", schema=None) as batch_op:
        batch_op.drop_column("dispensa_irrf_confirmada")
