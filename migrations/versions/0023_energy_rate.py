"""Energy: half-hourly or block electricity unit rates for the tariff in use.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "energy_rate",
        sa.Column("tariff_code", sa.Text(), nullable=False),
        sa.Column("valid_from", TS, nullable=False),
        sa.Column("valid_to", TS),  # null = open-ended (a fixed tariff's current rate)
        sa.Column("pence_inc_vat", sa.Float(), nullable=False),
        sa.Column("pence_exc_vat", sa.Float()),
        sa.Column("fetched_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("tariff_code", "valid_from"),
    )


def downgrade() -> None:
    op.drop_table("energy_rate")
