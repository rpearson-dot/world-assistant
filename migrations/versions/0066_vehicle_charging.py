"""vehicle.target_soc and vehicle.charge_boost: the charge target lives on the car.

The state of charge to stop charging at belongs to the car, not to a Home Assistant helper — and the one-off
"charge to 100% for a long drive" (the boost) is the car's too. The planner reads them from the electric vehicle
instead of input_number.wa_ev_target_soc. target_soc null means "use the usual 80%".

Revision ID: 0066
Revises: 0065
Create Date: 2026-11-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vehicle", sa.Column("target_soc", sa.Float()))
    op.add_column("vehicle", sa.Column("charge_boost", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("vehicle", "charge_boost")
    op.drop_column("vehicle", "target_soc")
