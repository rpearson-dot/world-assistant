"""Weather from Open-Meteo for your home place: each hour as forecast (the newest forecast of it) and as it turned out
(observed), for heating and solar planning.

Revision ID: 0053
Revises: 0052
Create Date: 2026-11-14
"""

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE weather_hour (
          place text NOT NULL, at timestamptz NOT NULL, source text NOT NULL, temp_c double precision,
          apparent_c double precision, precip_mm double precision, cloud_pct double precision, wind_kph double precision,
          radiation_wm2 double precision, code int, fetched_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (place, at, source)
        )""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS weather_hour")
