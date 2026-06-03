"""cria modulo de cotas rpv

Revision ID: b8c1d2e3f4a5
Revises: a4b3c2d1e9f0
Create Date: 2026-05-21 11:35:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b8c1d2e3f4a5"
down_revision = "a4b3c2d1e9f0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cotas_rpv_competencias",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("competencia", sa.String(length=7), nullable=False),
        sa.Column("grupo_cota", sa.String(length=20), nullable=False),
        sa.Column("criado_por_id", sa.Integer(), nullable=False),
        sa.Column("atualizado_por_id", sa.Integer(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["atualizado_por_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["criado_por_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "competencia",
            "grupo_cota",
            name="uq_cotas_rpv_competencia_grupo",
        ),
    )
    op.create_index(
        op.f("ix_cotas_rpv_competencias_competencia"),
        "cotas_rpv_competencias",
        ["competencia"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cotas_rpv_competencias_grupo_cota"),
        "cotas_rpv_competencias",
        ["grupo_cota"],
        unique=False,
    )

    op.create_table(
        "cotas_rpv_movimentos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cota_rpv_competencia_id", sa.Integer(), nullable=False),
        sa.Column("tipo_movimento", sa.String(length=30), nullable=False),
        sa.Column("valor", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("referencia_competencia", sa.String(length=7), nullable=True),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column("criado_por_id", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cota_rpv_competencia_id"],
            ["cotas_rpv_competencias.id"],
        ),
        sa.ForeignKeyConstraint(["criado_por_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_cotas_rpv_movimentos_cota_rpv_competencia_id"),
        "cotas_rpv_movimentos",
        ["cota_rpv_competencia_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cotas_rpv_movimentos_tipo_movimento"),
        "cotas_rpv_movimentos",
        ["tipo_movimento"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cotas_rpv_movimentos_criado_em"),
        "cotas_rpv_movimentos",
        ["criado_em"],
        unique=False,
    )

    op.create_table(
        "cotas_rpv_consumos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cota_rpv_competencia_id", sa.Integer(), nullable=False),
        sa.Column("origem_tipo", sa.String(length=30), nullable=False),
        sa.Column("origem_id", sa.Integer(), nullable=False),
        sa.Column("valor_consumido", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("resumo_origem", sa.String(length=255), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("consumido_por_id", sa.Integer(), nullable=False),
        sa.Column("estornado_por_id", sa.Integer(), nullable=True),
        sa.Column("consumido_em", sa.DateTime(), nullable=False),
        sa.Column("estornado_em", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["cota_rpv_competencia_id"],
            ["cotas_rpv_competencias.id"],
        ),
        sa.ForeignKeyConstraint(["consumido_por_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["estornado_por_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_cotas_rpv_consumos_cota_rpv_competencia_id"),
        "cotas_rpv_consumos",
        ["cota_rpv_competencia_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cotas_rpv_consumos_origem_id"),
        "cotas_rpv_consumos",
        ["origem_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cotas_rpv_consumos_origem_tipo"),
        "cotas_rpv_consumos",
        ["origem_tipo"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cotas_rpv_consumos_ativo"),
        "cotas_rpv_consumos",
        ["ativo"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cotas_rpv_consumos_consumido_em"),
        "cotas_rpv_consumos",
        ["consumido_em"],
        unique=False,
    )
    op.create_index(
        "ix_cotas_rpv_consumos_origem",
        "cotas_rpv_consumos",
        ["origem_tipo", "origem_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_cotas_rpv_consumos_origem", table_name="cotas_rpv_consumos")
    op.drop_index(op.f("ix_cotas_rpv_consumos_consumido_em"), table_name="cotas_rpv_consumos")
    op.drop_index(op.f("ix_cotas_rpv_consumos_ativo"), table_name="cotas_rpv_consumos")
    op.drop_index(op.f("ix_cotas_rpv_consumos_origem_tipo"), table_name="cotas_rpv_consumos")
    op.drop_index(op.f("ix_cotas_rpv_consumos_origem_id"), table_name="cotas_rpv_consumos")
    op.drop_index(
        op.f("ix_cotas_rpv_consumos_cota_rpv_competencia_id"),
        table_name="cotas_rpv_consumos",
    )
    op.drop_table("cotas_rpv_consumos")

    op.drop_index(op.f("ix_cotas_rpv_movimentos_criado_em"), table_name="cotas_rpv_movimentos")
    op.drop_index(
        op.f("ix_cotas_rpv_movimentos_tipo_movimento"),
        table_name="cotas_rpv_movimentos",
    )
    op.drop_index(
        op.f("ix_cotas_rpv_movimentos_cota_rpv_competencia_id"),
        table_name="cotas_rpv_movimentos",
    )
    op.drop_table("cotas_rpv_movimentos")

    op.drop_index(op.f("ix_cotas_rpv_competencias_grupo_cota"), table_name="cotas_rpv_competencias")
    op.drop_index(op.f("ix_cotas_rpv_competencias_competencia"), table_name="cotas_rpv_competencias")
    op.drop_table("cotas_rpv_competencias")
