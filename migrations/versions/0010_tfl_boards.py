"""tfl_board: saved departure boards (a station, its lines and a destination filter)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tfl_board",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("station", sa.Text(), nullable=False),  # StopPoint / NaPTAN id
        sa.Column("lines", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("include", postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default="{}"),  # destination must contain one of these
        sa.Column("exclude", postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default="{}"),  # destination must contain none of these
        sa.Column("max_rows", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("tfl_board")
