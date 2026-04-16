"""adiciona validacao por codigo em recuperacao de senha

Revision ID: 9e3b1a4d5c62
Revises: 4c1e8b2a9f71
Create Date: 2026-04-01 12:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9e3b1a4d5c62"
down_revision = "4c1e8b2a9f71"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "password_reset_tokens",
        sa.Column("verificado_em", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "password_reset_tokens",
        sa.Column(
            "tentativas_codigo",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade():
    op.drop_column("password_reset_tokens", "tentativas_codigo")
    op.drop_column("password_reset_tokens", "verificado_em")
