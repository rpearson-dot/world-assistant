"""The home network: each device seen (by its MAC address, with the address and name the router gave it), and what
it did: joining and leaving, its DNS lookups (from Pi-hole), and firewall decisions (from the UniFi gateway's
syslog). Events are kept 30 days by default; a day table by device keeps the counts for a year. Also the
manufacturers' tracker lists used to recognise what gets past Pi-hole, and the blocks you added through World Assistant.

Revision ID: 0058
Revises: 0057
Create Date: 2026-11-14
"""

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE net_device (
          mac text PRIMARY KEY, ip text, name text, alias text, first_seen timestamptz NOT NULL DEFAULT now(),
          last_seen timestamptz NOT NULL DEFAULT now(), last_what text, connection text, notified boolean NOT NULL DEFAULT false
        )""")
    op.execute("CREATE INDEX ix_net_device_ip ON net_device (ip)")
    op.execute("""
        CREATE TABLE net_event (
          id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at timestamptz NOT NULL, source text NOT NULL, kind text NOT NULL,
          mac text, ip text, action text, dest text, port int, proto text, detail jsonb
        )""")
    op.execute("CREATE INDEX ix_net_event_at ON net_event (at)")
    op.execute("CREATE INDEX ix_net_event_mac_at ON net_event (mac, at)")
    op.execute("CREATE INDEX ix_net_event_ip_at ON net_event (ip, at)")
    op.execute("""
        CREATE TABLE net_day (
          day date NOT NULL, mac text NOT NULL, kind text NOT NULL, action text NOT NULL, n int NOT NULL DEFAULT 0,
          PRIMARY KEY (day, mac, kind, action)
        )""")


    op.execute("""
        CREATE TABLE net_reference (
          domain text NOT NULL, list text NOT NULL, PRIMARY KEY (domain, list)
        )""")
    op.execute("""
        CREATE TABLE net_block (
          mac text NOT NULL, domain text NOT NULL, added_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (mac, domain)
        )""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS net_block, net_reference, net_day, net_event, net_device")
