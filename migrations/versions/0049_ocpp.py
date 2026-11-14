"""The charger's own central system (OCPP 1.6J): every message to and from the charger, its connections, boots and
faults, and its charging sessions. Only ever added to, apart from the charger's current state (ocpp_charger).

Revision ID: 0049
Revises: 0048
Create Date: 2026-11-13
"""

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One row per charger: what it said it is at boot, and how it stands now
    op.execute("""
        CREATE TABLE ocpp_charger (
          id text PRIMARY KEY, first_seen timestamptz NOT NULL DEFAULT now(), last_seen timestamptz,
          connected boolean NOT NULL DEFAULT false, connected_at timestamptz, boot jsonb NOT NULL DEFAULT '{}',
          booted_at timestamptz, status text, error_code text, status_info jsonb NOT NULL DEFAULT '{}',
          meter jsonb NOT NULL DEFAULT '{}', config jsonb NOT NULL DEFAULT '{}', heartbeat_s int
        )""")
    # Every OCPP frame either way, as it was sent (call, result or error), kept two years
    op.execute("""
        CREATE TABLE ocpp_message (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(),
          charger text NOT NULL, direction text NOT NULL, kind int NOT NULL, unique_id text, action text,
          payload jsonb, raw text NOT NULL
        )""")
    op.execute("CREATE INDEX ix_ocpp_message_at ON ocpp_message (charger, at)")
    op.execute("CREATE INDEX ix_ocpp_message_action ON ocpp_message (charger, action, at)")
    # What happened to the charger, in words: connected, disconnected (and how), booted, fault, status changes,
    # commands sent and their answers. The page's timeline, and how a reset is told from a dropped connection.
    op.execute("""
        CREATE TABLE ocpp_event (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(),
          charger text NOT NULL, kind text NOT NULL, detail jsonb NOT NULL DEFAULT '{}', text text NOT NULL
        )""")
    op.execute("CREATE INDEX ix_ocpp_event_at ON ocpp_event (charger, at)")
    # Charging sessions (OCPP transactions)
    op.execute("""
        CREATE TABLE ocpp_session (
          charger text NOT NULL, transaction_id int NOT NULL, connector int, id_tag text,
          started_at timestamptz NOT NULL, meter_start_wh double precision, stopped_at timestamptz,
          meter_stop_wh double precision, reason text, last_meter_wh double precision, last_power_w double precision,
          PRIMARY KEY (charger, transaction_id)
        )""")
    op.execute("CREATE INDEX ix_ocpp_session_started ON ocpp_session (charger, started_at)")


def downgrade() -> None:
    for t in ("ocpp_session", "ocpp_event", "ocpp_message", "ocpp_charger"):
        op.execute(f"DROP TABLE IF EXISTS {t}")
