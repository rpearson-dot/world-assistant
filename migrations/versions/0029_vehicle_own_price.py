"""vehicle: your own price per litre, for a fuel no station reports (LPG) or as a fallback

Fuel Finder covers petrol and diesel grades only, so an LPG vehicle's trip cost uses the price you give.

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vehicle", sa.Column("fuel_pence_per_litre", sa.Float()))


def downgrade() -> None:
    op.drop_column("vehicle", "fuel_pence_per_litre")
