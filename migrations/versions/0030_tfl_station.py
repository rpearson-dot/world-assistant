"""tfl_station, tfl_station_part, tfl_station_alert: TfL stations' layout, facilities and live closures

Stations (Underground, Overground, DLR, Elizabeth line, tram) with their lines, fare zone and facilities (lifts,
toilets, car park, step-free access); their entrances and platforms; and what is closed now (lifts out of
service, escalators, entrances), from TfL's StopPoint API.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-23
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)
GEOG = ("geography(Point, 4326) GENERATED ALWAYS AS "
        "(ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED")


def upgrade() -> None:
    op.create_table(
        "tfl_station",
        sa.Column("id", sa.Text(), primary_key=True),  # NaPTAN id, e.g. 940GZZLUWLO or 910GWIMBLDN
        sa.Column("hub", sa.Text()),  # stations in one building share a hub code, e.g. HUBWIM
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("modes", pg.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("lines", pg.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("zone", sa.Text()),
        sa.Column("step_free", sa.Text()),  # TfL's own wording, when it gives one
        sa.Column("facilities", pg.JSONB(), nullable=False, server_default="{}"),  # {category: {key: value}}
        sa.Column("entrances", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("platforms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("seen", TS, nullable=False),
    )
    op.execute(f"ALTER TABLE tfl_station ADD COLUMN geog {GEOG}")
    op.execute("CREATE INDEX ix_tfl_station_geog ON tfl_station USING GIST (geog)")
    op.create_index("ix_tfl_station_hub", "tfl_station", ["hub"])
    op.create_index("ix_tfl_station_name", "tfl_station", [sa.text("lower(name)")])

    op.create_table(
        "tfl_station_part",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("station_id", sa.Text(), sa.ForeignKey("tfl_station.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),  # entrance | platform
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("lines", pg.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("towards", sa.Text()),
        sa.Column("lat", sa.Float()),
        sa.Column("lon", sa.Float()),
    )
    op.create_index("ix_tfl_station_part_station", "tfl_station_part", ["station_id", "kind"])

    op.create_table(
        "tfl_station_alert",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("station_id", sa.Text(), nullable=False),  # not a foreign key: alerts may name a stop not loaded
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.Text(), nullable=False),  # lift | escalator | entrance | step_free | other
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("starts_at", TS),
        sa.Column("ends_at", TS),
        sa.Column("seen", TS, nullable=False),
    )
    op.create_index("ix_tfl_station_alert_station", "tfl_station_alert", ["station_id"])


def downgrade() -> None:
    op.drop_table("tfl_station_alert")
    op.drop_table("tfl_station_part")
    op.drop_table("tfl_station")
