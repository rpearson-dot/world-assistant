"""The bus log's school run, worked out once a day is over: each day's figures (for the reliability over time) and
the whole result, so a day already looked at is not worked out again.

Revision ID: 0060
Revises: 0059
Create Date: 2026-11-14
"""

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE buslog_schoolrun (
          line text NOT NULL, service_day date NOT NULL, computed_at timestamptz NOT NULL DEFAULT now(),
          version int NOT NULL, settings jsonb NOT NULL, summary jsonb NOT NULL, result jsonb NOT NULL,
          PRIMARY KEY (line, service_day)
        )""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS buslog_schoolrun")
