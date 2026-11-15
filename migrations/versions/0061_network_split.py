"""The Network module split in two: the UniFi gateway log (unifi) and Pi-hole (pihole), sharing the network devices
(network). A choice that had Network on keeps both on; one that had it off keeps both off.

Revision ID: 0061
Revises: 0060
Create Date: 2026-11-15
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def _change(add: tuple[str, ...], remove: tuple[str, ...]) -> None:
    conn = op.get_bind()
    v = conn.execute(sa.text("SELECT value FROM app_setting WHERE key = 'modules'")).scalar()
    v = json.loads(v) if isinstance(v, str) else v
    if not isinstance(v, dict):
        return
    for k in ("on", "off"):
        ids = list(v.get(k) or [])
        if "network" in ids:
            ids = [x for x in ids if x not in remove] + [x for x in add if x not in ids]
            v[k] = ids
    conn.execute(sa.text("UPDATE app_setting SET value = CAST(:v AS jsonb), updated_at = now() WHERE key = 'modules'"), {"v": json.dumps(v)})


def upgrade() -> None:
    _change(("unifi", "pihole"), ())


def downgrade() -> None:
    _change((), ("unifi", "pihole"))
