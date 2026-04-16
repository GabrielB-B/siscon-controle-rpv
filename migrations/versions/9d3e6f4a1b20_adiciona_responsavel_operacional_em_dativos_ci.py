"""adiciona responsavel operacional em dativos ci

Revision ID: 9d3e6f4a1b20
Revises: 5b7c3e91f2d4
Create Date: 2026-03-31 09:40:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9d3e6f4a1b20"
down_revision = "5b7c3e91f2d4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("dativos_ci", schema=None) as batch_op:
        batch_op.add_column(sa.Column("responsavel_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_dativos_ci_responsavel_id"),
            ["responsavel_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_dativos_ci_responsavel_id_usuarios",
            "usuarios",
            ["responsavel_id"],
            ["id"],
        )

    op.execute(
        "UPDATE dativos_ci "
        "SET responsavel_id = criado_por_id "
        "WHERE responsavel_id IS NULL"
    )

    with op.batch_alter_table("dativos_ci", schema=None) as batch_op:
        batch_op.alter_column(
            "responsavel_id",
            existing_type=sa.Integer(),
            nullable=False,
        )


def downgrade():
    with op.batch_alter_table("dativos_ci", schema=None) as batch_op:
        batch_op.drop_constraint("fk_dativos_ci_responsavel_id_usuarios", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_dativos_ci_responsavel_id"))
        batch_op.drop_column("responsavel_id")
