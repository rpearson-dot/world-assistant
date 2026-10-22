"""Energy: Octopus Saving Sessions and Free Electricity sessions.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "energy_session",
        sa.Column("kind", sa.Text(), nullable=False),  # saving | boost | free
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("start_at", TS, nullable=False),
        sa.Column("end_at", TS, nullable=False),
        sa.Column("octopoints_per_kwh", sa.Integer()),
        sa.Column("joined", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("regions", sa.Text()),  # comma-separated; null = everyone
        sa.Column("event_type", sa.Text()),
        sa.Column("fetched_at", TS, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("kind", "code"),
    )


def downgrade() -> None:
    op.drop_table("energy_session")
