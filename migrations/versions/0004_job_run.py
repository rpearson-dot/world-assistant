"""job_run: a record of each scheduled job run, for the status page

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_run",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("job", sa.Text(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_job_run_job", "job_run", ["job", "started_at"])


def downgrade() -> None:
    op.drop_table("job_run")
