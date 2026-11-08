"""vehicle.registration and vehicle.checks: a vehicle's number plate, and what DVLA and DVSA last said about it
(tax and MOT status and due dates, the last MOT's result and advisories, outstanding recalls)

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-08
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vehicle", sa.Column("registration", sa.Text()))
    op.add_column("vehicle", sa.Column("checks", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("vehicle", "checks")
    op.drop_column("vehicle", "registration")
