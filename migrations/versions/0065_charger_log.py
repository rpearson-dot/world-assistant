"""ocpp_charger.peer and ocpp_log: the charger's own syslog.

The charger can be pointed at World Assistant's syslog server (the same UDP port the UniFi gateway uses). Its lines are
not UniFi events, so they are kept apart, in ocpp_log, matched to a charger by the address it last connected from
(ocpp_charger.peer, set on every OCPP connection). Append-only; trimmed to a month by the charger watch.
"""

from __future__ import annotations

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ocpp_charger ADD COLUMN IF NOT EXISTS peer text")
    op.execute("""
        CREATE TABLE IF NOT EXISTS ocpp_log (
          id bigserial PRIMARY KEY,
          at timestamptz NOT NULL DEFAULT now(),
          charger text NOT NULL,
          facility int,
          severity int,
          message text NOT NULL,
          raw text NOT NULL,
          from_ip text
        )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ocpp_log_at ON ocpp_log (charger, at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ocpp_log")
    op.execute("ALTER TABLE ocpp_charger DROP COLUMN IF EXISTS peer")
