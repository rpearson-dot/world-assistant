"""vehicle: fuel type and real-world economy, for trip cost and detour maths

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vehicle",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="fuel"),  # fuel | ev
        sa.Column("fuel", sa.Text()),  # fuel vehicles: E10, B7_STANDARD, ...
        sa.Column("mpg", sa.Float()),  # fuel vehicles, UK gallons
        sa.Column("tank_litres", sa.Float()),
        sa.Column("battery_kwh", sa.Float()),  # EVs: nominal capacity
        sa.Column("soh_percent", sa.Float()),  # EVs: battery state of health
        sa.Column("miles_per_kwh", sa.Float()),  # EVs: real-world efficiency
        sa.Column("home_pence_per_kwh", sa.Float()),  # EVs: what a home charge costs you
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "(kind = 'fuel' AND fuel IS NOT NULL AND mpg IS NOT NULL) OR "
            "(kind = 'ev' AND battery_kwh IS NOT NULL AND miles_per_kwh IS NOT NULL)",
            name="ck_vehicle_kind_fields",
        ),
    )


def downgrade() -> None:
    op.drop_table("vehicle")
