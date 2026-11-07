"""Outdoors: the shop a Bikesy deal is at (Bikesy's links hide it, but each deal names it)

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outdoors_product", sa.Column("shop", sa.Text()))


def downgrade() -> None:
    op.drop_column("outdoors_product", "shop")
