"""Shopping: the product you mean for each list item at each retailer, and the six-hour store of searches

shopping_match: per KitchenOwl item (item_key "ko:<id>") and retailer, the product you confirmed.
shopping_search: the supermarket service's answer for one query at one retailer, reused for six hours.

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-04
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shopping_match",
        sa.Column("item_key", sa.Text(), primary_key=True),
        sa.Column("retailer", sa.Text(), primary_key=True),
        sa.Column("product", postgresql.JSONB(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "shopping_search",
        sa.Column("retailer", sa.Text(), primary_key=True),
        sa.Column("query", sa.Text(), primary_key=True),
        sa.Column("products", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("shopping_search")
    op.drop_table("shopping_match")
