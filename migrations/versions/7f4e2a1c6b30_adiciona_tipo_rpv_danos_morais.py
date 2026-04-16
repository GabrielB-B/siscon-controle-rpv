"""adiciona tipo de rpv danos morais

Revision ID: 7f4e2a1c6b30
Revises: 3e4a1d2c9b80
Create Date: 2026-04-01 09:10:00.000000
"""

from datetime import datetime

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7f4e2a1c6b30"
down_revision = "3e4a1d2c9b80"
branch_labels = None
depends_on = None


tipos_rpv = sa.table(
    "tipos_rpv",
    sa.column("id", sa.Integer()),
    sa.column("nome", sa.String(length=120)),
    sa.column("ativo", sa.Boolean()),
    sa.column("ordem_exibicao", sa.Integer()),
    sa.column("criado_em", sa.DateTime()),
    sa.column("atualizado_em", sa.DateTime()),
)

registros_rpv = sa.table(
    "registros_rpv",
    sa.column("id", sa.Integer()),
    sa.column("tipo_rpv_id", sa.Integer()),
)


def upgrade():
    bind = op.get_bind()
    agora = datetime.utcnow()

    bind.execute(
        sa.update(tipos_rpv)
        .where(tipos_rpv.c.nome == "RPV dativo")
        .values(ordem_exibicao=10, atualizado_em=agora)
    )

    danos_morais_id = bind.execute(
        sa.select(tipos_rpv.c.id).where(tipos_rpv.c.nome == "Danos Morais")
    ).scalar_one_or_none()

    if danos_morais_id is None:
        bind.execute(
            sa.insert(tipos_rpv).values(
                nome="Danos Morais",
                ativo=True,
                ordem_exibicao=9,
                criado_em=agora,
                atualizado_em=agora,
            )
        )
        return

    bind.execute(
        sa.update(tipos_rpv)
        .where(tipos_rpv.c.id == danos_morais_id)
        .values(
            ativo=True,
            ordem_exibicao=9,
            atualizado_em=agora,
        )
    )


def downgrade():
    bind = op.get_bind()
    agora = datetime.utcnow()

    danos_morais_id = bind.execute(
        sa.select(tipos_rpv.c.id).where(tipos_rpv.c.nome == "Danos Morais")
    ).scalar_one_or_none()

    if danos_morais_id is not None:
        total_registros = bind.execute(
            sa.select(sa.func.count(registros_rpv.c.id)).where(
                registros_rpv.c.tipo_rpv_id == danos_morais_id
            )
        ).scalar_one()

        if total_registros:
            bind.execute(
                sa.update(tipos_rpv)
                .where(tipos_rpv.c.id == danos_morais_id)
                .values(
                    ativo=False,
                    ordem_exibicao=None,
                    atualizado_em=agora,
                )
            )
        else:
            bind.execute(sa.delete(tipos_rpv).where(tipos_rpv.c.id == danos_morais_id))

    bind.execute(
        sa.update(tipos_rpv)
        .where(tipos_rpv.c.nome == "RPV dativo")
        .values(ordem_exibicao=9, atualizado_em=agora)
    )
