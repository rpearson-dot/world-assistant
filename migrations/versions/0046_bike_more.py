"""The bike garage, more: each ride's route (a few dozen points from its GPS track) and the devices' batteries Garmin
saw on it (SRAM AXS derailleurs), a reading with a logged measurement (a chain's stretch), and a photo of each bike's
frame number

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-08
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bike_ride", sa.Column("route", JSONB))  # [[lat, lon], ...] about every km, at most 200
    op.add_column("bike_ride", sa.Column("sensors", JSONB))  # [{"maker", "serial", "type", "status", "volts"}]
    op.add_column("bike_service", sa.Column("reading", sa.Float()))  # a measurement: chain stretch %, pad mm...
    op.add_column("bike", sa.Column("frame_photo", sa.LargeBinary()))


def downgrade() -> None:
    op.drop_column("bike", "frame_photo")
    op.drop_column("bike_service", "reading")
    op.drop_column("bike_ride", "sensors")
    op.drop_column("bike_ride", "route")
