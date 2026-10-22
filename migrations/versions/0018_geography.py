"""Put the PostGIS extension to work: an indexed point beside every lat/lon pair.

The extension has been installed since 0001 but nothing used it, so every distance was worked out
in Python after a bounding-box prefilter. Each table that holds coordinates gets a `geog` column
that Postgres keeps in step with lat and lon by itself, and a GiST index over it, so "within 10 km
of here, nearest first" becomes one indexed query.

The column is GENERATED, not a copy: it cannot drift, nothing has to remember to write it, and a
row with no coordinates gets NULL rather than a point in the Atlantic.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

# table -> the columns holding latitude and longitude
TABLES = {
    "place": ("lat", "lon"),
    "fuel_station": ("lat", "lon"),
    "nh_sign": ("lat", "lon"),
    "ha_location": ("lat", "lon"),
    "pollen_grid": ("lat", "lon"),
}


def _exists(table: str) -> bool:
    """Whether the table is in this database.

    A full upgrade has all five. Tests build a partial schema on purpose (one feature's tables, so
    one feature's tests stay independent), and a column added to a table that is not there would
    stop them for no good reason.
    """
    bind = op.get_bind()
    return bind.execute(sa.text("SELECT to_regclass(:t)"), {"t": table}).scalar() is not None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    for table, (lat, lon) in TABLES.items():
        if not _exists(table):
            continue
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN geog geography(Point, 4326) "
            f"GENERATED ALWAYS AS ("
            f"  CASE WHEN {lat} IS NULL OR {lon} IS NULL THEN NULL "
            f"       ELSE ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)::geography END"
            f") STORED"
        )
        op.execute(f"CREATE INDEX ix_{table}_geog ON {table} USING GIST (geog)")


def downgrade() -> None:
    for table in TABLES:
        if not _exists(table):
            continue
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_geog")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS geog")
