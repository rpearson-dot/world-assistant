"""bus tracking: trackers, stop sequences, leg times, anomalies and target-stop history

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "bus_tracker",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("state", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("state_updated", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "bus_stop",
        sa.Column("tracker", sa.Text(), sa.ForeignKey("bus_tracker.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("pos", sa.Integer(), nullable=False),
        sa.Column("sid", sa.Text(), nullable=False),
        sa.Column("name", sa.Text()),
        sa.PrimaryKeyConstraint("tracker", "direction", "pos"),
    )
    op.create_table(
        "bus_segment_obs",
        sa.Column("tracker", sa.Text(), sa.ForeignKey("bus_tracker.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vehicle", sa.Text(), nullable=False),
        sa.Column("from_stop", sa.Text(), nullable=False),
        sa.Column("to_stop", sa.Text(), nullable=False),
        sa.Column("observed_at", TS, nullable=False),
        sa.Column("seconds", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("tracker", "vehicle", "from_stop", "to_stop", "observed_at"),
    )
    op.create_index("ix_bus_segment_obs_time", "bus_segment_obs", ["tracker", "observed_at"])
    op.create_table(
        "bus_anomaly",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("tracker", sa.Text(), sa.ForeignKey("bus_tracker.id", ondelete="CASCADE"), nullable=False),
        sa.Column("at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("vehicle", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_bus_anomaly_time", "bus_anomaly", ["tracker", "at"])
    op.create_table(
        "bus_target_day",
        sa.Column("tracker", sa.Text(), sa.ForeignKey("bus_tracker.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("slots", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.PrimaryKeyConstraint("tracker", "day"),
    )
    op.create_table(
        "bus_target_hit",
        sa.Column("tracker", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("vehicle", sa.Text(), nullable=False),
        sa.Column("board_at", TS, nullable=False),
        sa.Column("arrive_at", TS, nullable=False),
        sa.Column("slot", sa.Text()),
        sa.Column("delay_min", sa.Float()),
        sa.Column("via_return", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("tracker", "day", "vehicle", "board_at"),
        sa.ForeignKeyConstraint(["tracker", "day"], ["bus_target_day.tracker", "bus_target_day.day"],
                                ondelete="CASCADE"),
    )
    op.create_table(
        "bus_timetable",
        sa.Column("tracker", sa.Text(), sa.ForeignKey("bus_tracker.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sid", sa.Text(), nullable=False),
        sa.Column("day_type", sa.Text(), nullable=False),
        sa.Column("times", postgresql.JSONB(), nullable=False),
        sa.Column("fetched_at", TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tracker", "sid", "day_type"),
    )


def downgrade() -> None:
    for t in ("bus_timetable", "bus_target_hit", "bus_target_day", "bus_anomaly", "bus_segment_obs",
              "bus_stop", "bus_tracker"):
        op.drop_table(t)
