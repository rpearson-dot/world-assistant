"""The bike garage: riders, bikes, labelled sets (wheels and other groups), parts, where each was fitted when,
rides, and the service log

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "bike_rider",
        sa.Column("id", sa.Text(), primary_key=True),  # a short name, made plain ("rob")
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("weight_kg", sa.Float()),
        sa.Column("settings", JSONB, nullable=False, server_default="{}"),
        sa.Column("created", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "bike",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rider_id", sa.Text(), sa.ForeignKey("bike_rider.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("config", JSONB, nullable=False),  # the answers: type, brakes, suspension, tyres...
        sa.Column("weight_kg", sa.Float()),
        sa.Column("garmin_gear", sa.Text()),
        sa.Column("records", JSONB, nullable=False, server_default="{}"),  # frame number, purchase, insurer
        sa.Column("photo", sa.LargeBinary()),
        sa.Column("photo_url", sa.Text()),
        sa.Column("created", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("retired_at", TS),
        sa.UniqueConstraint("rider_id", "label", name="uq_bike_label"),
    )
    op.create_table(
        "bike_set",  # a wheel, or any other labelled group of parts that moves together
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rider_id", sa.Text(), sa.ForeignKey("bike_rider.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),  # wheel | group
        sa.Column("position", sa.Text()),  # front | rear, for a wheel
        sa.Column("grp", sa.Text()),  # wheels sharing a group label swap together ("Winter wheels")
        sa.Column("created", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("retired_at", TS),
        sa.UniqueConstraint("rider_id", "label", name="uq_bike_set_label"),
    )
    op.create_table(
        "bike_part",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rider_id", sa.Text(), sa.ForeignKey("bike_rider.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),  # chain, cassette, pads, tyre, axs_battery, helmet...
        sa.Column("position", sa.Text()),  # front | rear | stoker | a battery slot
        sa.Column("label", sa.Text()),
        sa.Column("brand", sa.Text()),
        sa.Column("model", sa.Text()),
        sa.Column("url", sa.Text()),
        sa.Column("photo", sa.LargeBinary()),
        sa.Column("photo_url", sa.Text()),
        sa.Column("price", sa.Numeric(10, 2)),
        sa.Column("status", sa.Text(), nullable=False, server_default="fitted"),  # fitted | shelf | kit | retired
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),  # on the shelf
        sa.Column("fitted_at", TS),
        sa.Column("start_km", sa.Float(), nullable=False, server_default="0"),
        sa.Column("start_hours", sa.Float(), nullable=False, server_default="0"),
        sa.Column("life", JSONB),  # {"km":, "hours":, "days":}; none: the kind's default
        sa.Column("details", JSONB, nullable=False, server_default="{}"),  # tubeless, width, pressure, fluid...
        sa.Column("to_list_at", TS),  # when it was put on the Outdoors list
        sa.Column("notes", sa.Text()),
        sa.Column("created", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("retired_at", TS),
    )
    op.create_index("ix_bike_part_rider", "bike_part", ["rider_id", "status"])
    op.create_table(
        "bike_fit",  # where a part or a set was from a moment on: on a bike, in a set, or off (neither)
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item", sa.Text(), nullable=False),  # part | set
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("bike_id", sa.Integer(), sa.ForeignKey("bike.id", ondelete="CASCADE")),
        sa.Column("set_id", sa.Integer(), sa.ForeignKey("bike_set.id", ondelete="CASCADE")),
        sa.Column("at", TS, nullable=False),
    )
    op.create_index("ix_bike_fit_item", "bike_fit", ["item", "item_id", "at"])
    op.create_table(
        "bike_ride",
        sa.Column("id", sa.Text(), primary_key=True),  # "garmin:<id>", "csv:<date+title>" or "manual:<n>"
        sa.Column("rider_id", sa.Text(), sa.ForeignKey("bike_rider.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bike_id", sa.Integer(), sa.ForeignKey("bike.id", ondelete="SET NULL")),
        sa.Column("started", TS, nullable=False),
        sa.Column("km", sa.Float(), nullable=False),
        sa.Column("hours", sa.Float(), nullable=False, server_default="0"),
        sa.Column("indoor", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("wet", sa.Boolean()),  # what the rider said: wet or not (none: not asked yet)
        sa.Column("wet_suggested", sa.Boolean()),  # from the rain at the time and place (none: not known)
        sa.Column("wet_why", sa.Text()),  # what the suggestion went on ("2.4 mm at Epsom gauge")
        sa.Column("lat", sa.Float()),
        sa.Column("lon", sa.Float()),
        sa.Column("garmin_bike", sa.Text()),  # the bike (gear) Garmin had for the ride: often left as the last one
        sa.Column("bike_checked", sa.Boolean(), nullable=False, server_default=sa.false()),  # the rider confirmed the bike
        sa.Column("activity", sa.Text()),
        sa.Column("title", sa.Text()),
        sa.Column("source", sa.Text(), nullable=False),
    )
    op.create_index("ix_bike_ride_rider", "bike_ride", ["rider_id", "started"])
    op.create_table(
        "bike_service",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rider_id", sa.Text(), sa.ForeignKey("bike_rider.id", ondelete="CASCADE"), nullable=False),
        sa.Column("part_id", sa.Integer(), sa.ForeignKey("bike_part.id", ondelete="CASCADE")),
        sa.Column("bike_id", sa.Integer(), sa.ForeignKey("bike.id", ondelete="CASCADE")),
        sa.Column("at", TS, nullable=False),
        sa.Column("action", sa.Text(), nullable=False),  # replaced, serviced, checked, charged, topped_up, crash, cost
        sa.Column("resets", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("level_before", sa.Float()),  # a battery's charge (%) when it was put on charge
        sa.Column("new_part_id", sa.Integer(), sa.ForeignKey("bike_part.id", ondelete="SET NULL")),  # what replaced it
        sa.Column("spare_id", sa.Integer(), sa.ForeignKey("bike_part.id", ondelete="SET NULL")),  # the shelf spare it used
        sa.Column("cost", sa.Numeric(10, 2)),
        sa.Column("note", sa.Text()),
    )
    op.create_index("ix_bike_service_part", "bike_service", ["part_id", "at"])


def downgrade() -> None:
    for t in ("bike_service", "bike_ride", "bike_fit", "bike_part", "bike_set", "bike", "bike_rider"):
        op.drop_table(t)
