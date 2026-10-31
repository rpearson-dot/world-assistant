"""vehicle.connector: the rapid-charging connector an electric vehicle takes (CCS, CHAdeMO, Type 2)

The EV charger answers use it when no connector is asked for.

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-31
"""
import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vehicle", sa.Column("connector", sa.Text()))


def downgrade() -> None:
    op.drop_column("vehicle", "connector")
