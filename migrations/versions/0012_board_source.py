"""tfl_board.source: which rail data source a board reads (tfl or nationalrail)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tfl_board", sa.Column("source", sa.Text(), nullable=False, server_default="tfl"))


def downgrade() -> None:
    op.drop_column("tfl_board", "source")
