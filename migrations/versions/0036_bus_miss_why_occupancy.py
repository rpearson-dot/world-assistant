"""bus_target_day.explained: why each scheduled bus that did not reach the target stop missed;
bus_position.occupancy: how full each bus said it was


{slot: {vehicle, kind, why}}, rewritten each poll while the day's window is open: the bus (by its vehicle id) that
left the boarding stop for that slot and how far it got (turned back where, stopped reporting where, arrived after
the deadline), or the bus that turned back before reaching the boarding stop, or that no bus was seen for it.

bus_position.occupancy (full, standingAvailable, seatsAvailable; NULL: the bus did not say) and occupancy_counts
(seated, wheelchair... counts when an operator adds them): optional in the Bus Open Data feed and rarely published
yet, so kept from every GPS report, to be there from the day an operator starts.

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-03
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bus_target_day", sa.Column("explained", pg.JSONB(), nullable=False, server_default="{}"))
    op.add_column("bus_position", sa.Column("occupancy", sa.Text()))
    op.add_column("bus_position", sa.Column("occupancy_counts", pg.JSONB(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("bus_position", "occupancy_counts")
    op.drop_column("bus_position", "occupancy")
    op.drop_column("bus_target_day", "explained")
