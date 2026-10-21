"""Remove sign message rows that repeat the previous message's text

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-20
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM nh_sign_message m USING (
          SELECT id, lines, lag(lines) OVER (PARTITION BY sign_id ORDER BY set_at, id) AS prev
          FROM nh_sign_message
        ) x
        WHERE m.id = x.id AND x.lines = x.prev
        """
    )


def downgrade() -> None:
    pass
