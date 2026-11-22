"""Remember the item name a shopping match belongs to, so a confirmed "usual" product survives the item being
ticked off and re-added in KitchenOwl (which gives it a new id). Matches are then found by id or by name, the same
way the wishlist and outdoors modules already do.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shopping_match", sa.Column("item_name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("shopping_match", "item_name")
