"""places and trips: named locations and the journeys between them

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "place",
        sa.Column("id", sa.Text(), primary_key=True),  # slug, e.g. "home"
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="other"),
        sa.Column("postcode", sa.Text()),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "trip",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "from_place", sa.Text(), sa.ForeignKey("place.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "to_place", sa.Text(), sa.ForeignKey("place.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        # Filled once a route has been turned into a corridor
        sa.Column("corridor_id", sa.Text(), sa.ForeignKey("nh_corridor.id", ondelete="SET NULL")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("from_place", "to_place", name="uq_trip_places"),
        sa.CheckConstraint("from_place <> to_place", name="ck_trip_distinct"),
    )


def downgrade() -> None:
    op.drop_table("trip")
    op.drop_table("place")
