"""Solar forecasts from forecast.solar: each array's expected output for every period of today and tomorrow (the
newest forecast of it), and each day's total as forecast the day before, to check against what the panels made.

Revision ID: 0056
Revises: 0055
Create Date: 2026-11-14
"""

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE solar_forecast (
          plane text NOT NULL, period_start timestamptz NOT NULL, watts double precision, wh double precision,
          fetched_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (plane, period_start)
        )""")
    op.execute("""
        CREATE TABLE solar_day (
          day date PRIMARY KEY, day_ahead_kwh double precision, morning_kwh double precision,
          updated_at timestamptz NOT NULL DEFAULT now()
        )""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS solar_day")
    op.execute("DROP TABLE IF EXISTS solar_forecast")
