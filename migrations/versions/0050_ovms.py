"""The car's own module (OVMS) read over MQTT: each metric as it stands, the history of the ones worth keeping, and
every notification the module sends (alerts, info, errors). Only the latest values are ever updated.

Revision ID: 0050
Revises: 0049
Create Date: 2026-11-14
"""

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ovms_metric (
          vehicle text NOT NULL, metric text NOT NULL, value text, updated_at timestamptz NOT NULL,
          PRIMARY KEY (vehicle, metric)
        )""")
    op.execute("""
        CREATE TABLE ovms_metric_log (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at timestamptz NOT NULL, vehicle text NOT NULL,
          metric text NOT NULL, value text
        )""")
    op.execute("CREATE INDEX ix_ovms_metric_log ON ovms_metric_log (vehicle, metric, at)")
    op.execute("""
        CREATE TABLE ovms_notice (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at timestamptz NOT NULL DEFAULT now(), vehicle text NOT NULL,
          kind text NOT NULL, subtype text, text text NOT NULL
        )""")
    op.execute("CREATE INDEX ix_ovms_notice ON ovms_notice (vehicle, at)")


def downgrade() -> None:
    for t in ("ovms_notice", "ovms_metric_log", "ovms_metric"):
        op.execute(f"DROP TABLE IF EXISTS {t}")
