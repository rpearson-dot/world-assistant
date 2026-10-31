"""ev_site, ev_evse, ev_tariff: public EV chargers (GRIDSERVE's open data API first)

ev_site: a charging site (a location, in OCPI terms) with its position, facilities and opening times. ev_evse: one
charger at a site (an EVSE), its live status and its connectors ({standard, kind, kw, tariffs}). ev_tariff: a price
per kWh, with VAT. `operator` keeps other networks' feeds apart when they are added. Each row keeps the record as
published (`detail`), so a field this app does not read yet is not lost.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-31
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)
GEOG = ("geography(Point, 4326) GENERATED ALWAYS AS "
        "(CASE WHEN lat IS NULL OR lon IS NULL THEN NULL "
        "ELSE ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography END) STORED")


def upgrade() -> None:
    op.create_table(
        "ev_site",
        sa.Column("id", sa.Text(), primary_key=True),  # "<operator>:<location id>"
        sa.Column("operator", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("network", sa.Text()),  # e.g. Electric Highway
        sa.Column("address", sa.Text()),
        sa.Column("city", sa.Text()),
        sa.Column("postcode", sa.Text()),
        sa.Column("lat", sa.Float()),
        sa.Column("lon", sa.Float()),
        sa.Column("facilities", pg.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("open_24h", sa.Boolean()),
        sa.Column("opening", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("updated_at", TS),  # as the operator says
        sa.Column("detail", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("seen", TS, nullable=False),
    )
    op.execute(f"ALTER TABLE ev_site ADD COLUMN geog {GEOG}")
    op.execute("CREATE INDEX ix_ev_site_geog ON ev_site USING GIST (geog)")
    op.create_table(
        "ev_evse",
        sa.Column("id", sa.Text(), primary_key=True),  # "<operator>:<evse uid>"
        sa.Column("site_id", sa.Text(), sa.ForeignKey("ev_site.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),  # OCPI: AVAILABLE, CHARGING, OUT_OF_ORDER, ...
        sa.Column("connectors", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("max_kw", sa.Float()),
        sa.Column("updated_at", TS),
        sa.Column("detail", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("seen", TS, nullable=False),
    )
    op.create_index("ix_ev_evse_site", "ev_evse", ["site_id"])
    op.create_table(
        "ev_tariff",
        sa.Column("id", sa.Text(), primary_key=True),  # "<operator>:<tariff id>"
        sa.Column("currency", sa.Text()),
        sa.Column("pence_per_kwh", sa.Float()),  # with VAT
        sa.Column("detail", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("seen", TS, nullable=False),
    )


def downgrade() -> None:
    for t in ("ev_tariff", "ev_evse", "ev_site"):
        op.drop_table(t)
