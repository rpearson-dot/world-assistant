"""raw_capture: raw upstream responses kept temporarily to learn payload shapes

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_capture",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "captured_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("call_name", sa.Text(), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.Column("request_path", sa.Text(), nullable=False),
        sa.Column("request_params", postgresql.JSONB(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("x_next", sa.Text(), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("body_bytes", sa.Integer(), nullable=True),
        sa.Column("body", postgresql.JSONB(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_raw_capture_run", "raw_capture", ["run_id", "call_name", "page_index"])
    op.create_index("ix_raw_capture_captured_at", "raw_capture", ["captured_at"])


def downgrade() -> None:
    op.drop_table("raw_capture")
