"""What's on near your places (Skiddle and Ticketmaster), kept so it can be related to places, districts and roads.

The events feature asks the providers live and caches for minutes. The knowledge graph and anything that wants
"events in this district" needs them held, so a job copies the next two weeks around each saved place here, and
drops them once they are over. `geog` is generated from lat and lon, as in 0020.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "local_event",
        sa.Column("id", sa.Text(), primary_key=True),  # "<source>:<provider id>"
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("starts", sa.Text()),
        sa.Column("type", sa.Text()),
        sa.Column("venue", sa.Text()),
        sa.Column("town", sa.Text()),
        sa.Column("postcode", sa.Text()),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("price", sa.Text()),
        sa.Column("url", sa.Text()),
        sa.Column("seen", TS, nullable=False),
    )
    op.execute(
        "ALTER TABLE local_event ADD COLUMN geog geography(Point, 4326) "
        "GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography) STORED"
    )
    op.execute("CREATE INDEX ix_local_event_geog ON local_event USING GIST (geog)")
    op.create_index("ix_local_event_day", "local_event", ["day"])


def downgrade() -> None:
    op.drop_table("local_event")
