"""unifica heads e blinda duplicidade dativos

Revision ID: 2c4f6a8b9d10
Revises: 1f3c2d7ab901, 6c2b1d4e8f90
Create Date: 2026-03-27 12:30:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "2c4f6a8b9d10"
down_revision = ("1f3c2d7ab901", "6c2b1d4e8f90")
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("dativos_itens", schema=None) as batch_op:
        batch_op.create_index(
            "uq_dativos_itens_ci_grupo_doc_processo",
            ["dativo_ci_id", "grupo", "cpf_normalizado", "numero_processo"],
            unique=True,
        )


def downgrade():
    with op.batch_alter_table("dativos_itens", schema=None) as batch_op:
        batch_op.drop_index("uq_dativos_itens_ci_grupo_doc_processo")
