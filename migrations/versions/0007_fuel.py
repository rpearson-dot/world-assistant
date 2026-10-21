"""fuel: stations, current prices, and a log of price changes

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "fuel_station",
        sa.Column("node_id", sa.Text(), primary_key=True),
        sa.Column("trading_name", sa.Text()),
        sa.Column("brand_name", sa.Text()),
        sa.Column("is_motorway", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_supermarket", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("temporary_closure", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("permanent_closure", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("phone", sa.Text()),
        sa.Column("address_line_1", sa.Text()),
        sa.Column("city", sa.Text()),
        sa.Column("county", sa.Text()),
        sa.Column("country", sa.Text()),
        sa.Column("postcode", sa.Text()),
        sa.Column("lat", sa.Float()),
        sa.Column("lon", sa.Float()),
        sa.Column("coords_ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("amenities", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("fuel_types", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("opening_times", postgresql.JSONB()),
        sa.Column("first_seen", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_fuel_station_geo", "fuel_station", ["lat", "lon"])

    op.create_table(
        "fuel_price",
        sa.Column(
            "node_id",
            sa.Text(),
            sa.ForeignKey("fuel_station.node_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("fuel_type", sa.Text(), primary_key=True),
        sa.Column("pence", sa.Float(), nullable=False),
        sa.Column("last_updated", TS),
        sa.Column("effective", TS),
        sa.Column("observed_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_fuel_price_type", "fuel_price", ["fuel_type", "pence"])

    # One row each time a station's price changes, as seen by a poll (not every poll).
    op.create_table(
        "fuel_price_change",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("fuel_type", sa.Text(), nullable=False),
        sa.Column("pence", sa.Float(), nullable=False),
        sa.Column("effective", TS),
        sa.Column("observed_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_fuel_change", "fuel_price_change", ["node_id", "fuel_type", "observed_at"])


def downgrade() -> None:
    op.drop_table("fuel_price_change")
    op.drop_table("fuel_price")
    op.drop_table("fuel_station")
