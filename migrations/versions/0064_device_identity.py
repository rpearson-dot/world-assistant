"""A confirmed identity for a network device: what it actually is, set by you (accepting Discovery's guess or typing
your own), so the guess becomes a fact. Kept on the device record beside its name and alias.

Revision ID: 0064
Revises: 0063
Create Date: 2026-11-16
"""

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Named confirmed_kind (not kind): net_day already has a kind column, and net_device joins it, so a bare "kind"
    # would be an ambiguous reference in those queries.
    op.execute("ALTER TABLE net_device ADD COLUMN IF NOT EXISTS confirmed_kind text")
    op.execute("ALTER TABLE net_device ADD COLUMN IF NOT EXISTS confirmed_at timestamptz")


def downgrade() -> None:
    op.execute("ALTER TABLE net_device DROP COLUMN IF EXISTS confirmed_at")
    op.execute("ALTER TABLE net_device DROP COLUMN IF EXISTS confirmed_kind")
