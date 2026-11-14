"""The home energy planner: each charging decision World Assistant would make (or made), beside Home Assistant's plan
at the same moment, so the two can be compared before World Assistant takes over.

Revision ID: 0055
Revises: 0054
Create Date: 2026-11-14
"""

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE planner_decision (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(), plan text NOT NULL,
          why text NOT NULL, ha_plan text, mode text, acted text, inputs jsonb NOT NULL DEFAULT '{}'
        )""")
    op.execute("CREATE INDEX ix_planner_decision_at ON planner_decision (at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS planner_decision")
