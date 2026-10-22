"""Postcodes held locally, from Ordnance Survey's Code-Point Open.

Code-Point Open gives each postcode a position on the Ordnance Survey national grid (eastings and
northings, EPSG:27700). Everything else here is WGS84, so latitude, longitude and the indexed point
are all GENERATED from the grid reference by PostGIS, the way 0018 did for the other tables. They
cannot drift, and the loader only ever writes what the file says.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)
WGS84 = "ST_Transform(ST_SetSRID(ST_MakePoint(easting, northing), 27700), 4326)"


def upgrade() -> None:
    op.create_table(
        "postcode",
        sa.Column("pc", sa.Text(), primary_key=True),  # no spaces, upper case: SW1A2AA
        sa.Column("postcode", sa.Text(), nullable=False),  # as written: SW1A 2AA
        sa.Column("easting", sa.Integer(), nullable=False),
        sa.Column("northing", sa.Integer(), nullable=False),
        sa.Column("quality", sa.SmallInteger()),  # 10 best (a delivery point) .. 80; 90 has no position
        sa.Column("country", sa.Text()),  # ONS code, e.g. E92000001
        sa.Column("district", sa.Text()),
        sa.Column("ward", sa.Text()),
        sa.Column("seen", TS, nullable=False),
    )
    op.execute(f"ALTER TABLE postcode ADD COLUMN lat double precision GENERATED ALWAYS AS (ST_Y({WGS84})) STORED")
    op.execute(f"ALTER TABLE postcode ADD COLUMN lon double precision GENERATED ALWAYS AS (ST_X({WGS84})) STORED")
    op.execute(f"ALTER TABLE postcode ADD COLUMN geog geography(Point, 4326) GENERATED ALWAYS AS ({WGS84}::geography) STORED")
    op.execute("CREATE INDEX ix_postcode_geog ON postcode USING GIST (geog)")
    op.create_index("ix_postcode_seen", "postcode", ["seen"])


def downgrade() -> None:
    op.drop_table("postcode")
