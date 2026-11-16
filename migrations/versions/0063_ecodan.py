"""Ecodan heat-pump readings: hot-water tank temperature and target, the boost state, flow/return and outdoor
temperatures, the heating zone, power and daily energy, kept for charting and (later) the PV-to-hot-water decisions.
Read-only tracking through MELCloud.

Revision ID: 0063
Revises: 0062
Create Date: 2026-11-16
"""

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ecodan_reading (
          device text NOT NULL,
          at timestamptz NOT NULL,
          name text,
          tank_c double precision,
          tank_target_c double precision,
          forced_hot_water boolean,
          flow_c double precision,
          return_c double precision,
          outdoor_c double precision,
          zone1_room_c double precision,
          zone1_set_c double precision,
          power boolean,
          operation_mode integer,
          heating_in_kwh double precision,
          water_in_kwh double precision,
          heating_out_kwh double precision,
          water_out_kwh double precision,
          offline boolean,
          PRIMARY KEY (device, at)
        )""")
    op.execute("CREATE INDEX ecodan_reading_at ON ecodan_reading (at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ecodan_reading")
