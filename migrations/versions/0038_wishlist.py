"""Wishlist: every time something on the KitchenOwl "Wishlist" list was seen at Aldi or Lidl

The wishes themselves live in KitchenOwl, so the whole family can add and tick them. This keeps what World Assistant
found for each: one row per product per retailer that matched an item (item_key "ko:<KitchenOwl item id>"), with the
first and last time it turned up in a check, and the price, on-sale date and availability the retailer gave then.
KitchenOwl keeps an item's id when it is ticked and added back, so its history comes back with it.

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wishlist_seen",
        sa.Column("item_key", sa.Text(), primary_key=True),
        sa.Column("retailer", sa.Text(), primary_key=True),
        sa.Column("product_id", sa.Text(), primary_key=True),
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("brand", sa.Text()),
        sa.Column("price", sa.Numeric(10, 2)),
        sa.Column("url", sa.Text()),
        sa.Column("on_sale", sa.Date()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("availability", sa.Text()),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("wishlist_seen_name", "wishlist_seen", [sa.text("lower(item_name)")])


def downgrade() -> None:
    op.drop_index("wishlist_seen_name", table_name="wishlist_seen")
    op.drop_table("wishlist_seen")
