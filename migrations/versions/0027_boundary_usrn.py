"""Ordnance Survey open data: administrative boundaries (Boundary-Line) and street references (Open USRN).

`boundary` holds the areas a place, stop or road event falls in: regions, counties, districts and boroughs
(optionally wards, parishes and constituencies), simplified so a district is a few hundred points, not
tens of thousands. `usrn` holds each street's reference number and line, the key roadworks (Street
Manager) are reported against. Both are reference data, reloaded from the OS download when it changes.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "boundary",
        sa.Column("id", sa.Text(), primary_key=True),  # "<kind>:<GSS code or name>"
        sa.Column("kind", sa.Text(), nullable=False),  # region, county, district, ward, parish, constituency
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("code", sa.Text()),
        sa.Column("seen", TS, nullable=False),
    )
    op.execute("ALTER TABLE boundary ADD COLUMN geom geometry(MultiPolygon, 4326) NOT NULL")
    op.execute("CREATE INDEX ix_boundary_geom ON boundary USING GIST (geom)")
    op.create_index("ix_boundary_kind", "boundary", ["kind"])
    op.create_table(
        "usrn",
        sa.Column("usrn", sa.BigInteger(), primary_key=True),
        sa.Column("seen", TS, nullable=False),
    )
    op.execute("ALTER TABLE usrn ADD COLUMN geom geometry(MultiLineString, 4326) NOT NULL")
    op.execute("CREATE INDEX ix_usrn_geom ON usrn USING GIST (geom)")


def downgrade() -> None:
    op.drop_table("usrn")
    op.drop_table("boundary")
