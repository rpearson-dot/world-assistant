"""Outside modules from GitHub repositories: each one installed (its release, manifest, what you agreed, its
settings, held update and warnings), the runner's job queue, what it published to Home Assistant (to take back on
removal) and a log of what happened, including each piece of personal data it was given.

Revision ID: 0057
Revises: 0056
Create Date: 2026-11-14
"""

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE xmod (
          id text PRIMARY KEY, repository text NOT NULL, tag text, version text, commit text, ref text,
          manifest jsonb NOT NULL DEFAULT '{}', consent jsonb, settings jsonb NOT NULL DEFAULT '{}',
          state text NOT NULL, message text, auto_update boolean NOT NULL DEFAULT true, allow_vulnerable boolean NOT NULL DEFAULT false,
          checks jsonb, installed_checks jsonb, candidate jsonb, held jsonb, warnings jsonb NOT NULL DEFAULT '[]',
          usage jsonb, token_hash text, last_release_at timestamptz, checked_at timestamptz,
          installed_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now()
        )""")
    op.execute("""
        CREATE TABLE xmod_job (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, module text NOT NULL, kind text NOT NULL,
          spec jsonb NOT NULL DEFAULT '{}', purpose text, state text NOT NULL DEFAULT 'waiting', result jsonb,
          created_at timestamptz NOT NULL DEFAULT now(), taken_at timestamptz, done_at timestamptz
        )""")
    op.execute("CREATE INDEX ix_xmod_job_waiting ON xmod_job (state, id)")
    op.execute("CREATE TABLE xmod_published (module text NOT NULL, topic text NOT NULL, PRIMARY KEY (module, topic))")
    op.execute("""
        CREATE TABLE xmod_event (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(), module text NOT NULL,
          kind text NOT NULL, text text, data jsonb
        )""")
    op.execute("CREATE INDEX ix_xmod_event ON xmod_event (module, at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS xmod_event, xmod_published, xmod_job, xmod")
