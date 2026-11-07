"""Bus disruptions anywhere, by where they are: every point a disruption names (its stops and places)

Until now only the disruptions near a saved place were kept. Here can be anywhere, so every current disruption is kept
with its points; `near_place` is still set for those near a saved place (and only those are listed as "near your
places").

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE bus_disruption_point (
            disruption_id text NOT NULL REFERENCES bus_disruption(id) ON DELETE CASCADE,
            geog geography(Point, 4326) NOT NULL
        )""")
    op.execute("CREATE INDEX ix_bus_disruption_point_geog ON bus_disruption_point USING gist (geog)")
    op.create_index("ix_bus_disruption_point_id", "bus_disruption_point", ["disruption_id"])
    _ = sa  # the table is plain SQL for the geography type


def downgrade() -> None:
    op.drop_table("bus_disruption_point")
