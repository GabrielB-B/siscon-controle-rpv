"""cria pendencias documentais de rpv

Revision ID: a4b3c2d1e9f0
Revises: d1f8a4c6b2e0
Create Date: 2026-04-07 15:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a4b3c2d1e9f0"
down_revision = "d1f8a4c6b2e0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rpv_pendencias_documentais",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("exercicio", sa.String(length=7), nullable=False),
        sa.Column("processo_edoc", sa.String(length=50), nullable=False),
        sa.Column("numero_processo", sa.String(length=50), nullable=False),
        sa.Column("data_ci", sa.Date(), nullable=False),
        sa.Column("tipo_rpv_id", sa.Integer(), nullable=False),
        sa.Column("responsavel_id", sa.Integer(), nullable=False),
        sa.Column("nome_beneficiario", sa.String(length=200), nullable=False),
        sa.Column("nome_beneficiario_normalizado", sa.String(length=200), nullable=False),
        sa.Column("tipo_documento", sa.String(length=10), nullable=False),
        sa.Column("documento_original", sa.String(length=30), nullable=True),
        sa.Column("documento_normalizado", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("valor_bruto", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("valor_irrf", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("sem_irrf", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column(
            "motivo_pendencia",
            sa.String(length=255),
            nullable=False,
            server_default="Documento pendente de validacao.",
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="aberta"),
        sa.Column(
            "documento_confirmado_manual",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("documento_confirmado_em", sa.DateTime(), nullable=True),
        sa.Column("documento_confirmado_por_id", sa.Integer(), nullable=True),
        sa.Column("registro_rpv_convertido_id", sa.Integer(), nullable=True),
        sa.Column("criado_por_id", sa.Integer(), nullable=False),
        sa.Column("atualizado_por_id", sa.Integer(), nullable=True),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["atualizado_por_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["criado_por_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["documento_confirmado_por_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["registro_rpv_convertido_id"], ["registros_rpv.id"]),
        sa.ForeignKeyConstraint(["responsavel_id"], ["usuarios.id"]),
        sa.ForeignKeyConstraint(["tipo_rpv_id"], ["tipos_rpv.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_rpv_pendencias_documentais_documento_normalizado"),
        "rpv_pendencias_documentais",
        ["documento_normalizado"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rpv_pendencias_documentais_exercicio"),
        "rpv_pendencias_documentais",
        ["exercicio"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rpv_pendencias_documentais_nome_beneficiario_normalizado"),
        "rpv_pendencias_documentais",
        ["nome_beneficiario_normalizado"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rpv_pendencias_documentais_numero_processo"),
        "rpv_pendencias_documentais",
        ["numero_processo"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rpv_pendencias_documentais_processo_edoc"),
        "rpv_pendencias_documentais",
        ["processo_edoc"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rpv_pendencias_documentais_responsavel_id"),
        "rpv_pendencias_documentais",
        ["responsavel_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rpv_pendencias_documentais_status"),
        "rpv_pendencias_documentais",
        ["status"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_rpv_pendencias_documentais_status"),
        table_name="rpv_pendencias_documentais",
    )
    op.drop_index(
        op.f("ix_rpv_pendencias_documentais_responsavel_id"),
        table_name="rpv_pendencias_documentais",
    )
    op.drop_index(
        op.f("ix_rpv_pendencias_documentais_processo_edoc"),
        table_name="rpv_pendencias_documentais",
    )
    op.drop_index(
        op.f("ix_rpv_pendencias_documentais_numero_processo"),
        table_name="rpv_pendencias_documentais",
    )
    op.drop_index(
        op.f("ix_rpv_pendencias_documentais_nome_beneficiario_normalizado"),
        table_name="rpv_pendencias_documentais",
    )
    op.drop_index(
        op.f("ix_rpv_pendencias_documentais_exercicio"),
        table_name="rpv_pendencias_documentais",
    )
    op.drop_index(
        op.f("ix_rpv_pendencias_documentais_documento_normalizado"),
        table_name="rpv_pendencias_documentais",
    )
    op.drop_table("rpv_pendencias_documentais")
