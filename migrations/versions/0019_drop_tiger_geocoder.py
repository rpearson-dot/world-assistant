"""Drop the US census geocoder that the PostGIS image installs by default.

`postgis/postgis` creates postgis, postgis_topology, fuzzystrmatch and postgis_tiger_geocoder. The
last of those is for geocoding United States census addresses. Nothing here geocodes anything —
postcodes go to postcodes.io and routes to Valhalla — and it is actively in the way: it adds `tiger`
to the database's search path and ships its own `place` table there, so an unqualified `place` can
resolve to the census one instead of ours. Ours only wins because `public` happens to come first.

Dropping it removes 34 empty tables and the shadowing. The search path is pinned to `public`
afterwards, because the extension put `tiger` on it and removing the extension does not take it off.

`fuzzystrmatch` is left alone: it is small, it shadows nothing, and the geocoder required it.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS postgis_tiger_geocoder CASCADE")
    db = op.get_bind().execute(sa.text("SELECT current_database()")).scalar_one()
    op.execute(f'ALTER DATABASE "{db}" SET search_path TO public')


def downgrade() -> None:
    """Put it back as the image would have, search path included."""
    db = op.get_bind().execute(sa.text("SELECT current_database()")).scalar_one()
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis_tiger_geocoder CASCADE")
    op.execute(f'ALTER DATABASE "{db}" SET search_path TO "$user", public, tiger')
