"""trip route summary: distance, base driving time, roads, when it was built

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trip", sa.Column("distance_km", sa.Float()))
    op.add_column("trip", sa.Column("duration_s", sa.Integer()))
    op.add_column("trip", sa.Column("roads", postgresql.ARRAY(sa.Text())))
    op.add_column("trip", sa.Column("route_built_at", sa.TIMESTAMP(timezone=True)))


def downgrade() -> None:
    for col in ("route_built_at", "roads", "duration_s", "distance_km"):
        op.drop_column("trip", col)
