"""app_setting: small switches the user flips from the status page

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-21
"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_setting",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("app_setting")
