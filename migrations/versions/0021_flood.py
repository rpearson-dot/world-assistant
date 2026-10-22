"""Flood warnings: the Environment Agency's current warnings, and the areas they apply to.

A warning is small and changes every few minutes; the area it applies to is a polygon that never
changes, so the two live apart. Polygons are fetched once, the first time a warning needs one, and
kept (the API's own advice is to keep a local copy of the boundaries).

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "flood_area",
        sa.Column("notation", sa.Text(), primary_key=True),
        sa.Column("label", sa.Text()),
        sa.Column("county", sa.Text()),
        sa.Column("river_or_sea", sa.Text()),
        sa.Column("ea_area", sa.Text()),
        sa.Column("polygon_url", sa.Text()),
        sa.Column("geom_at", TS),
    )
    op.execute("ALTER TABLE flood_area ADD COLUMN geom geography(Geometry, 4326)")
    op.execute("CREATE INDEX ix_flood_area_geom ON flood_area USING GIST (geom)")
    op.create_table(
        "flood_warning",
        sa.Column("id", sa.Text(), primary_key=True),  # floodAreaID: a warning's URI is stable per area
        sa.Column("area", sa.Text(), nullable=False),  # flood_area.notation
        sa.Column("severity_level", sa.SmallInteger(), nullable=False),
        sa.Column("severity", sa.Text()),
        sa.Column("message", sa.Text()),
        sa.Column("is_tidal", sa.Boolean()),
        sa.Column("raised_at", TS),
        sa.Column("severity_changed_at", TS),
        sa.Column("message_changed_at", TS),
        sa.Column("seen", TS, nullable=False),
    )
    op.create_index("ix_flood_warning_level", "flood_warning", ["severity_level"])


def downgrade() -> None:
    op.drop_table("flood_warning")
    op.drop_table("flood_area")
