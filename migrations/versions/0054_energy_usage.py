"""The electricity meter's half-hourly readings from Octopus: what was bought (import) and sold (export).

Revision ID: 0054
Revises: 0053
Create Date: 2026-11-14
"""

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE energy_usage (
          direction text NOT NULL, interval_start timestamptz NOT NULL, kwh double precision NOT NULL, mpan text NOT NULL,
          fetched_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (direction, interval_start)
        )""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS energy_usage")
