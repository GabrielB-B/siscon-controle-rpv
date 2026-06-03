"""adiciona status em dativos ci

Revision ID: c1d4e7f8a9b0
Revises: b8c1d2e3f4a5
Create Date: 2026-05-27 11:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c1d4e7f8a9b0"
down_revision = "b8c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("dativos_ci", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=20),
                nullable=False,
                server_default="aberta",
            )
        )
        batch_op.create_index("ix_dativos_ci_status", ["status"], unique=False)

    op.execute("UPDATE dativos_ci SET status = 'aberta' WHERE status IS NULL OR TRIM(status) = ''")


def downgrade():
    with op.batch_alter_table("dativos_ci", schema=None) as batch_op:
        batch_op.drop_index("ix_dativos_ci_status")
        batch_op.drop_column("status")
