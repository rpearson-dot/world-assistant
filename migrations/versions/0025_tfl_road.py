"""TfL road disruptions (works, collisions, closures) across London, with where they are.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "tfl_road_disruption",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("category", sa.Text()),
        sa.Column("sub_category", sa.Text()),
        sa.Column("severity", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("scope", sa.Text(), nullable=False),  # planned | unplanned
        sa.Column("location", sa.Text()),
        sa.Column("comment", sa.Text()),
        sa.Column("update_text", sa.Text()),
        sa.Column("starts_at", TS),
        sa.Column("ends_at", TS),
        sa.Column("has_closures", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("corridors", sa.Text()),
        sa.Column("last_modified", TS),
        sa.Column("seen", TS, nullable=False),
    )
    op.execute("ALTER TABLE tfl_road_disruption ADD COLUMN geom geography(Geometry, 4326)")
    op.execute("CREATE INDEX ix_tfl_road_geom ON tfl_road_disruption USING GIST (geom)")
    op.create_index("ix_tfl_road_time", "tfl_road_disruption", ["starts_at", "ends_at"])


def downgrade() -> None:
    op.drop_table("tfl_road_disruption")
