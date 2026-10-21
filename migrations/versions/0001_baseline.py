"""baseline: PostGIS extension and adapter cursor state

Revision ID: 0001
Revises:
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # Per-adapter cursors and small bits of state (last sync timestamp, etc.)
    op.create_table(
        "adapter_state",
        sa.Column("adapter", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("adapter", "key"),
    )


def downgrade() -> None:
    op.drop_table("adapter_state")
    # The postgis extension is left in place on purpose.
