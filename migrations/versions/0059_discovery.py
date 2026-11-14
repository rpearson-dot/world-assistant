"""Discovery: the IEEE register of MAC address prefixes (which company made a network adapter), to say what a device
on your network probably is.

Revision ID: 0059
Revises: 0058
Create Date: 2026-11-14
"""

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE net_oui (prefix text PRIMARY KEY, vendor text NOT NULL)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS net_oui")
