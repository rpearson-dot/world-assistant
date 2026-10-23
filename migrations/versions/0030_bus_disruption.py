"""bus_disruption: bus, tram and ferry disruptions local authorities publish to BODS, near your places

Only those within a distance of a saved place are kept; the whole set is replaced on each read.

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


def upgrade() -> None:
    op.create_table(
        "bus_disruption",
        sa.Column("id", sa.Text(), primary_key=True),  # "<publisher>:<situation number>"
        sa.Column("publisher", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("reason", sa.Text()),
        sa.Column("planned", sa.Boolean()),
        sa.Column("starts_at", TS),
        sa.Column("ends_at", TS),  # null: until further notice
        sa.Column("lines", pg.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("operators", pg.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("stops", pg.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("near_place", sa.Text()),  # the saved place it is closest to
        sa.Column("distance_km", sa.Float()),
        sa.Column("lat", sa.Float()),  # its point closest to that place
        sa.Column("lon", sa.Float()),
        sa.Column("seen", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_bus_disruption_place", "bus_disruption", ["near_place", "distance_km"])


def downgrade() -> None:
    op.drop_table("bus_disruption")
