"""UK Power Networks: power cuts, street works, the network around each place, and local carbon intensity

ukpn_fault: planned and unplanned power cuts now. ukpn_works: their street and road works, open now or proposed.
ukpn_place_area: for each saved place, what of the network it sits in (licence boundary, independent operator
area, grid supply point, primary and secondary substation, rota load disconnection block). ukpn_carbon: half-hourly
carbon intensity at the grid supply points your places are fed from. Each row keeps the record as published
(`detail`), so a field this app does not read yet is not lost.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-24
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)
GEOG = ("geography(Point, 4326) GENERATED ALWAYS AS "
        "(CASE WHEN lat IS NULL OR lon IS NULL THEN NULL "
        "ELSE ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography END) STORED")


def upgrade() -> None:
    op.create_table(
        "ukpn_fault",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("planned", sa.Boolean()),
        sa.Column("status", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("postcodes", pg.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("customers", sa.Integer()),
        sa.Column("started_at", TS),
        sa.Column("restore_at", TS),  # estimated restoration
        sa.Column("lat", sa.Float()),
        sa.Column("lon", sa.Float()),
        sa.Column("detail", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("seen", TS, nullable=False),
    )
    op.execute(f"ALTER TABLE ukpn_fault ADD COLUMN geog {GEOG}")
    op.execute("CREATE INDEX ix_ukpn_fault_geog ON ukpn_fault USING GIST (geog)")
    op.execute("CREATE INDEX ix_ukpn_fault_postcodes ON ukpn_fault USING GIN (postcodes)")

    # Every power cut ever seen, kept: when it was first and last seen, when it ended (left the feed), the worst
    # count of homes, the restoration estimates first and last given, and which saved places it was at.
    op.create_table(
        "ukpn_fault_history",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("planned", sa.Boolean()),
        sa.Column("description", sa.Text()),
        sa.Column("postcodes", pg.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("customers_max", sa.Integer()),
        sa.Column("started_at", TS),
        sa.Column("first_restore_at", TS),
        sa.Column("last_restore_at", TS),
        sa.Column("status_last", sa.Text()),
        sa.Column("first_seen", TS, nullable=False),
        sa.Column("last_seen", TS, nullable=False),
        sa.Column("ended_at", TS),  # null while it is still in the feed
        sa.Column("places", pg.ARRAY(sa.Text()), nullable=False, server_default="{}"),  # saved places it was at
        sa.Column("home", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("lat", sa.Float()),
        sa.Column("lon", sa.Float()),
    )
    op.create_index("ix_ukpn_fault_history_started", "ukpn_fault_history", ["first_seen"])
    op.execute("CREATE INDEX ix_ukpn_fault_history_places ON ukpn_fault_history USING GIN (places)")

    op.create_table(
        "ukpn_works",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("stage", sa.Text(), nullable=False),  # open | proposed
        sa.Column("street", sa.Text()),
        sa.Column("town", sa.Text()),
        sa.Column("usrn", sa.Text()),
        sa.Column("category", sa.Text()),
        sa.Column("traffic", sa.Text()),  # traffic management, e.g. road closure, lights
        sa.Column("description", sa.Text()),
        sa.Column("starts_at", TS),
        sa.Column("ends_at", TS),
        sa.Column("lat", sa.Float()),
        sa.Column("lon", sa.Float()),
        sa.Column("detail", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("seen", TS, nullable=False),
    )
    op.execute(f"ALTER TABLE ukpn_works ADD COLUMN geog {GEOG}")
    op.execute("CREATE INDEX ix_ukpn_works_geog ON ukpn_works USING GIST (geog)")
    op.create_index("ix_ukpn_works_stage", "ukpn_works", ["stage", "starts_at"])

    op.create_table(
        "ukpn_place_area",
        sa.Column("place_id", sa.Text(), sa.ForeignKey("place.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),  # boundary | idno | gsp | primary | secondary | rota
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("detail", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("seen", TS, nullable=False),
        sa.PrimaryKeyConstraint("place_id", "kind", "code"),
    )
    op.create_table(
        "ukpn_carbon",
        sa.Column("gsp", sa.Text(), nullable=False),
        sa.Column("at", TS, nullable=False),
        sa.Column("intensity", sa.Float(), nullable=False),  # gCO2/kWh
        sa.PrimaryKeyConstraint("gsp", "at"),
    )


def downgrade() -> None:
    for t in ("ukpn_carbon", "ukpn_place_area", "ukpn_works", "ukpn_fault_history", "ukpn_fault"):
        op.drop_table(t)
