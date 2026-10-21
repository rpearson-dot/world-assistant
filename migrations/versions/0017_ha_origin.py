"""place.origin and trip.origin: mark the places and trips that Home Assistant created

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-21
"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("place", sa.Column("origin", sa.Text(), nullable=True))
    op.add_column("trip", sa.Column("origin", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("trip", "origin")
    op.drop_column("place", "origin")
