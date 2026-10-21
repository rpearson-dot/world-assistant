"""pollen_reading: one value per place, day and allergen, kept for 90 days

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-21
"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "pollen_reading",
        sa.Column("place_id", sa.Text(), sa.ForeignKey("place.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        # An allergen id from the service, or "_risk" for the 0-10 allergy risk
        sa.Column("allergen", sa.Text(), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("recorded_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("place_id", "day", "allergen"),
    )
    op.create_index("ix_pollen_reading_day", "pollen_reading", ["day"])


def downgrade() -> None:
    op.drop_table("pollen_reading")
