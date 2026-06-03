"""adiciona indices de performance para bi e reinf

Revision ID: f6a2b9c4d3e1
Revises: c1d4e7f8a9b0
Create Date: 2026-06-01 10:47:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "f6a2b9c4d3e1"
down_revision = "c1d4e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("registros_rpv", schema=None) as batch_op:
        batch_op.create_index(
            "ix_registros_rpv_data_pagamento",
            ["data_pagamento"],
            unique=False,
        )
        batch_op.create_index(
            "ix_registros_rpv_elaborador_data_pagamento",
            ["elaborador_id", "data_pagamento"],
            unique=False,
        )

    with op.batch_alter_table("dativos_itens", schema=None) as batch_op:
        batch_op.create_index(
            "ix_dativos_itens_data_pagamento",
            ["data_pagamento"],
            unique=False,
        )
        batch_op.create_index(
            "ix_dativos_itens_grupo_data_pagamento",
            ["grupo", "data_pagamento"],
            unique=False,
        )
        batch_op.drop_index("ix_dativos_itens_grupo")


def downgrade():
    with op.batch_alter_table("dativos_itens", schema=None) as batch_op:
        batch_op.create_index("ix_dativos_itens_grupo", ["grupo"], unique=False)
        batch_op.drop_index("ix_dativos_itens_grupo_data_pagamento")
        batch_op.drop_index("ix_dativos_itens_data_pagamento")

    with op.batch_alter_table("registros_rpv", schema=None) as batch_op:
        batch_op.drop_index("ix_registros_rpv_elaborador_data_pagamento")
        batch_op.drop_index("ix_registros_rpv_data_pagamento")
