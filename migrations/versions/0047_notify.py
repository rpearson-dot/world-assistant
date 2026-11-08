"""Notifications to your phone (ntfy): each one sent, so none is sent twice and Settings can show the last few

Revision ID: 0047
Revises: 0046
Create Date: 2026-11-08
"""
import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notify_log",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False, unique=True),  # what it was about, e.g. battery-low:12:2026-11-08
        sa.Column("kind", sa.Text(), nullable=False),  # bikes, firmware, environment, system, test
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_notify_log_sent_at", "notify_log", ["sent_at"])


def downgrade() -> None:
    op.drop_table("notify_log")
