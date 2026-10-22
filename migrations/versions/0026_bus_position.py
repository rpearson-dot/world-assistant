"""Bus tracker: GPS positions (from the Bus Open Data Service) of the buses being followed, inside the journey window.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "bus_position",
        sa.Column("tracker", sa.Text(), sa.ForeignKey("bus_tracker.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vehicle", sa.Text(), nullable=False),
        sa.Column("recorded_at", TS, nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("bearing", sa.Float()),
        sa.Column("destination", sa.Text()),
        sa.Column("bods_direction", sa.Text()),
        sa.PrimaryKeyConstraint("tracker", "vehicle", "recorded_at"),
    )
    op.create_index("ix_bus_position_time", "bus_position", ["tracker", "recorded_at"])


def downgrade() -> None:
    op.drop_table("bus_position")
