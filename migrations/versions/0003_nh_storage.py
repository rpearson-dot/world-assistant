"""National Highways storage: corridors, events, signs and sign message history

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "nh_corridor",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        # [[lat, lon], ...] waypoints along the route
        sa.Column("centreline", postgresql.JSONB(), nullable=False),
        sa.Column("buffer_m", sa.Float(), nullable=False, server_default="500"),
        # Road numbers to accept (empty means any road)
        sa.Column("roads", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.Text(), nullable=False, server_default="manual"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "nh_event",
        sa.Column("record_id", sa.Text(), primary_key=True),
        sa.Column("scope", sa.Text(), nullable=False),  # unplanned or planned feed
        sa.Column("kind", sa.Text(), nullable=False),  # closure or speed
        sa.Column("situation_id", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("start_time", TS),
        sa.Column("end_time", TS),
        sa.Column("cause", sa.Text()),
        sa.Column("detail", sa.Text()),
        sa.Column("limit_mph", sa.Integer()),
        sa.Column("roads", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("segments", postgresql.JSONB(), nullable=False),
        sa.Column("version_time", TS),
        sa.Column("first_seen", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("cleared_at", TS),
    )
    op.create_index("ix_nh_event_live", "nh_event", ["scope", "kind", "cleared_at"])
    op.create_index("ix_nh_event_time", "nh_event", ["start_time", "end_time"])

    op.create_table(
        "nh_event_corridor",
        sa.Column(
            "record_id",
            sa.Text(),
            sa.ForeignKey("nh_event.record_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "corridor_id",
            sa.Text(),
            sa.ForeignKey("nh_corridor.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_nh_event_corridor_c", "nh_event_corridor", ["corridor_id"])

    op.create_table(
        "nh_sign",
        sa.Column("sign_id", sa.Text(), primary_key=True),
        sa.Column("road", sa.Text()),
        sa.Column("direction", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("lat", sa.Float()),
        sa.Column("lon", sa.Float()),
        sa.Column("working", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lines", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("time_last_set", TS),
        sa.Column("last_seen", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_nh_sign_road", "nh_sign", ["road", "direction"])

    op.create_table(
        "nh_sign_message",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "sign_id",
            sa.Text(),
            sa.ForeignKey("nh_sign.sign_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("set_at", TS, nullable=False),
        sa.Column("lines", postgresql.JSONB(), nullable=False),
        sa.Column("first_seen", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("sign_id", "set_at", name="uq_nh_sign_message"),
    )


def downgrade() -> None:
    op.drop_table("nh_sign_message")
    op.drop_table("nh_sign")
    op.drop_table("nh_event_corridor")
    op.drop_table("nh_event")
    op.drop_table("nh_corridor")
