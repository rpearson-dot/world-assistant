"""pollen_grid: the latest forecast for each point of a lattice over Britain

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-21
"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pollen_grid",
        sa.Column("lat", sa.Float(), primary_key=True),
        sa.Column("lon", sa.Float(), primary_key=True),
        sa.Column("forecast", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),  # changes when the forecast changes
        sa.Column("fetched_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("changed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("pollen_grid")
