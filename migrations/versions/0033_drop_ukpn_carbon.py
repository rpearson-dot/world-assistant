"""Drop ukpn_carbon: carbon intensity is no longer read (0.21.1)

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-25
"""
import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ukpn_carbon")


def downgrade() -> None:
    op.create_table(
        "ukpn_carbon",
        sa.Column("gsp", sa.Text(), nullable=False),
        sa.Column("at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("intensity", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("gsp", "at"),
    )
