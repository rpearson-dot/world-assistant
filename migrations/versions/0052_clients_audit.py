"""One token per client (a phone's Shortcuts, Home Assistant, an AI assistant, a script), each with only the access it
needs and revocable on its own; and an audit log of every change made through the API, by whom.

Revision ID: 0052
Revises: 0051
Create Date: 2026-11-14
"""

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only a hash of each token is kept: the token itself is shown once, when it is made
    op.execute("""
        CREATE TABLE api_client (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, name text NOT NULL UNIQUE, token_hash text NOT NULL UNIQUE,
          hint text NOT NULL, access text NOT NULL, paths jsonb NOT NULL DEFAULT '[]', created_at timestamptz NOT NULL DEFAULT now(),
          last_used_at timestamptz, revoked_at timestamptz
        )""")
    op.execute("""
        CREATE TABLE audit_log (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(), client text NOT NULL,
          method text NOT NULL, path text NOT NULL, status int NOT NULL, peer text
        )""")
    op.execute("CREATE INDEX ix_audit_log_at ON audit_log (at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_log")
    op.execute("DROP TABLE IF EXISTS api_client")
