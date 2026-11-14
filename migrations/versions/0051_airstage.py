"""Fujitsu Airstage air conditioners on the home network: each unit's readings every few minutes (only added to).

Revision ID: 0051
Revises: 0050
Create Date: 2026-11-14
"""

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE airstage_reading (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(), unit text NOT NULL,
          ok boolean NOT NULL, error text, power boolean, mode text, target_c double precision, indoor_c double precision,
          outdoor_c double precision, fan text, energy double precision, error_code text, raw jsonb NOT NULL DEFAULT '{}'
        )""")
    op.execute("CREATE INDEX ix_airstage_reading ON airstage_reading (unit, at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS airstage_reading")
