"""NaPTAN: where every bus stop, station and ferry terminal in Great Britain is.

The bus tracker stores each stop's id (an ATCO code) and name but never its position, so a stop
could not be drawn, measured from, or related to a saved place. NaPTAN is the national register
those ids come from; TfL's stop ids are its ATCO codes. Holding the register locally turns the id
into a point.

`geog` is generated from lat and lon, as in 0018, so it cannot drift and needs no maintenance.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "naptan_stop",
        sa.Column("atco", sa.Text(), primary_key=True),
        sa.Column("naptan_code", sa.Text()),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("street", sa.Text()),
        sa.Column("indicator", sa.Text()),
        sa.Column("locality", sa.Text()),
        sa.Column("stop_type", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("seen", TS, nullable=False),
    )
    op.execute(
        "ALTER TABLE naptan_stop ADD COLUMN geog geography(Point, 4326) "
        "GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED"
    )
    op.execute("CREATE INDEX ix_naptan_stop_geog ON naptan_stop USING GIST (geog)")
    op.create_index("ix_naptan_stop_kind", "naptan_stop", ["kind"])
    op.create_index("ix_naptan_stop_seen", "naptan_stop", ["seen"])


def downgrade() -> None:
    op.drop_table("naptan_stop")
