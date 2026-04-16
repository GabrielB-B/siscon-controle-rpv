"""adiciona campos de identidade em usuarios

Revision ID: 6c2b1d4e8f90
Revises: 4a9b8c7d6e5f
Create Date: 2026-03-24 18:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6c2b1d4e8f90"
down_revision = "4a9b8c7d6e5f"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("usuarios", schema=None) as batch_op:
        batch_op.add_column(sa.Column("email", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("telefone", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("cargo", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("setor", sa.String(length=80), nullable=True))
        batch_op.add_column(
            sa.Column("forcar_troca_senha", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("senha_alterada_em", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("ultimo_login_em", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("ultimo_login_ip", sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f("ix_usuarios_email"), ["email"], unique=True)

    with op.batch_alter_table("usuarios", schema=None) as batch_op:
        batch_op.alter_column("forcar_troca_senha", server_default=None)


def downgrade():
    with op.batch_alter_table("usuarios", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_usuarios_email"))
        batch_op.drop_column("ultimo_login_ip")
        batch_op.drop_column("ultimo_login_em")
        batch_op.drop_column("senha_alterada_em")
        batch_op.drop_column("forcar_troca_senha")
        batch_op.drop_column("setor")
        batch_op.drop_column("cargo")
        batch_op.drop_column("telefone")
        batch_op.drop_column("email")
