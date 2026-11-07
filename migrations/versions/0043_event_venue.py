"""Event venues, kept for the graph after their events end; when an event ends, and which venue it is at

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "event_venue",
        sa.Column("id", sa.Text(), primary_key=True),  # name and postcode (or position), made plain
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("town", sa.Text()),
        sa.Column("postcode", sa.Text()),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("first_seen", TS, nullable=False),
        sa.Column("last_seen", TS, nullable=False),
    )
    op.execute(
        "ALTER TABLE event_venue ADD COLUMN geog geography(Point, 4326) "
        "GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED"
    )
    op.execute("CREATE INDEX ix_event_venue_geog ON event_venue USING GIST (geog)")
    op.add_column("local_event", sa.Column("ends", TS))
    op.add_column("local_event", sa.Column("venue_id", sa.Text()))


def downgrade() -> None:
    op.drop_column("local_event", "venue_id")
    op.drop_column("local_event", "ends")
    op.drop_table("event_venue")
