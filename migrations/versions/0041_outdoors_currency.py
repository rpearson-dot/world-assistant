"""Outdoors: the currency a price is in (Tradeinn may answer in euros), and when a product last could not be read

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outdoors_product", sa.Column("currency", sa.Text(), nullable=False, server_default="GBP"))
    op.add_column("outdoors_product", sa.Column("error_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("outdoors_product", "error_at")
    op.drop_column("outdoors_product", "currency")
