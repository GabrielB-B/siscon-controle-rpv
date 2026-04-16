"""permite duplicidade confirmada em dativos

Revision ID: 5b7c3e91f2d4
Revises: 2c4f6a8b9d10
Create Date: 2026-03-30 10:20:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "5b7c3e91f2d4"
down_revision = "2c4f6a8b9d10"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("dativos_itens", schema=None) as batch_op:
        batch_op.drop_index("uq_dativos_itens_ci_grupo_doc_processo")
        batch_op.create_index(
            "ix_dativos_itens_ci_grupo_doc_processo",
            ["dativo_ci_id", "grupo", "cpf_normalizado", "numero_processo"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("dativos_itens", schema=None) as batch_op:
        batch_op.drop_index("ix_dativos_itens_ci_grupo_doc_processo")
        batch_op.create_index(
            "uq_dativos_itens_ci_grupo_doc_processo",
            ["dativo_ci_id", "grupo", "cpf_normalizado", "numero_processo"],
            unique=True,
        )
