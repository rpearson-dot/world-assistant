"""Outdoors: prices of the things on the KitchenOwl "Outdoors" list at GO Outdoors and Decathlon

outdoors_product: one row per product per retailer that an item follows (item_key "ko:<KitchenOwl item id>"): the
price now and the one before it, the lowest seen and when, the "was" price the shop shows, stock, and when the price
last fell (what Home Assistant's price-drop sensor counts).
outdoors_price: each price a product had, kept when it changes, for its history.

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outdoors_product",
        sa.Column("item_key", sa.Text(), primary_key=True),
        sa.Column("retailer", sa.Text(), primary_key=True),
        sa.Column("product_id", sa.Text(), primary_key=True),
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("brand", sa.Text()),
        sa.Column("url", sa.Text()),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("price", sa.Numeric(10, 2)),
        sa.Column("previous_price", sa.Numeric(10, 2)),
        sa.Column("was_price", sa.Numeric(10, 2)),
        sa.Column("lowest_price", sa.Numeric(10, 2)),
        sa.Column("lowest_at", sa.DateTime(timezone=True)),
        sa.Column("available", sa.Boolean()),
        sa.Column("dropped_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "outdoors_price",
        sa.Column("item_key", sa.Text(), nullable=False),
        sa.Column("retailer", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("outdoors_price_product", "outdoors_price", ["item_key", "retailer", "product_id", "at"])


def downgrade() -> None:
    op.drop_index("outdoors_price_product", table_name="outdoors_price")
    op.drop_table("outdoors_price")
    op.drop_table("outdoors_product")
