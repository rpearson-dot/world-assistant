"""Nest thermostat readings: room temperature, humidity, target, mode and whether it is calling for heat, kept for
charting and the heating forecast. Read-only tracking through Google's Smart Device Management API.

Revision ID: 0062
Revises: 0061
Create Date: 2026-11-15
"""

from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE nest_reading (
          device text NOT NULL,
          at timestamptz NOT NULL,
          name text,
          ambient_c double precision,
          humidity double precision,
          setpoint_c double precision,
          mode text,
          hvac text,
          eco text,
          online boolean,
          PRIMARY KEY (device, at)
        )""")
    op.execute("CREATE INDEX nest_reading_at ON nest_reading (at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS nest_reading")
