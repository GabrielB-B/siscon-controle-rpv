"""cria tabela de historico de alteracoes

Revision ID: 1f3c2d7ab901
Revises: 1675bc314225
Create Date: 2026-03-27 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1f3c2d7ab901"
down_revision = "1675bc314225"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "historico_alteracoes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entidade_tipo", sa.String(length=50), nullable=False),
        sa.Column("entidade_id", sa.Integer(), nullable=False),
        sa.Column("acao", sa.String(length=80), nullable=False),
        sa.Column("resumo", sa.String(length=255), nullable=True),
        sa.Column("detalhes_json", sa.Text(), nullable=False),
        sa.Column("alterado_por_id", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["alterado_por_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("historico_alteracoes", schema=None) as batch_op:
        batch_op.create_index(
            "ix_historico_alteracoes_entidade_tipo",
            ["entidade_tipo"],
            unique=False,
        )
        batch_op.create_index(
            "ix_historico_alteracoes_entidade_id",
            ["entidade_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_historico_alteracoes_criado_em",
            ["criado_em"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("historico_alteracoes", schema=None) as batch_op:
        batch_op.drop_index("ix_historico_alteracoes_criado_em")
        batch_op.drop_index("ix_historico_alteracoes_entidade_id")
        batch_op.drop_index("ix_historico_alteracoes_entidade_tipo")

    op.drop_table("historico_alteracoes")
