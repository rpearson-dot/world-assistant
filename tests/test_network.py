"""The network module: what the UniFi gateway sends read into events, Pi-hole's queries, suggested blocks for what
gets past Pi-hole's lists, blocking in Pi-hole without moving a device out of your groups (and Undo), and (with a
database) events kept against the right device with a new device noticed once."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from app.modules.network import parse, pihole, suggest

URL = os.environ.get("WA_TEST_DATABASE_URL")
NOW = datetime(2026, 11, 14, 16, tzinfo=UTC)
DHCP = "<30>Nov 14 16:00:01 UDR dnsmasq-dhcp[1234]: DHCPACK(br0) 10.0.2.50 aa:bb:cc:dd:ee:ff Living-Room-TV"
FW = ("<4>Nov 14 16:00:02 UDR kernel: [WAN_OUT-D-2147483647] IN=br0 OUT=eth4 MAC=11:22:33:44:55:66:aa:bb:cc:dd:ee:ff:08:00 "
      "SRC=10.0.2.50 DST=8.8.8.8 LEN=60 PROTO=UDP SPT=5353 DPT=53")  # fmt: skip
CEF = ("<14>1 2026-11-14T16:00:03Z UDR UniFi - - - CEF:0|Ubiquiti|UniFi Network|11.0.99|400|WiFi Client Connected|1|"
       "UNIFIclientMac=AA:BB:CC:DD:EE:FF UNIFIclientIp=10.0.2.50 UNIFIclientHostname=Living Room TV UNIFIwifiName=Home")  # fmt: skip


def test_what_the_gateway_sends() -> None:
    d = parse.line(DHCP, NOW)
    assert (d["kind"], d["action"], d["ip"], d["mac"], d["name"]) == ("dhcp", "lease", "10.0.2.50", "aa:bb:cc:dd:ee:ff", "Living-Room-TV")
    f = parse.line(FW, NOW)
    assert (f["kind"], f["action"], f["mac"], f["dest"], f["port"], f["proto"]) == ("firewall", "block", "aa:bb:cc:dd:ee:ff", "8.8.8.8", 53, "udp")
    assert parse.line(FW.replace("-D-", "-A-"), NOW)["action"] == "allow"
    c = parse.line(CEF, NOW)
    assert (c["kind"], c["action"], c["mac"], c["ip"], c["name"], c["detail"]["ssid"]) == ("client", "join", "aa:bb:cc:dd:ee:ff", "10.0.2.50", "Living Room TV", "Home")
    assert parse.line(CEF.replace("Connected", "Disconnected"), NOW)["action"] == "leave"
    o = parse.line("<13>Nov 14 16:00:04 UDR ubios-udapi-server: something else", NOW)
    assert o["kind"] == "system" and o["detail"] == {"program": "ubios-udapi-server", "text": "something else"}


def test_pihole_queries_become_events() -> None:
    e = parse.pihole({"id": 9, "time": NOW.timestamp(), "type": "A", "domain": "ads.example.com", "status": "GRAVITY",
                      "client": {"ip": "10.0.2.50", "name": "tv"}})  # fmt: skip
    assert (e["kind"], e["action"], e["dest"], e["ip"], e["at"]) == ("dns", "block", "ads.example.com", "10.0.2.50", NOW)
    assert parse.pihole({"domain": "bbc.co.uk", "status": "FORWARDED", "client": {"ip": "10.0.2.9"}})["action"] == "allow"


def test_suggestions_for_what_gets_past() -> None:
    rows = [{"domain": "lgtvsdp.com", "n": 700, "hours": 24}, {"domain": "time.google.com", "n": 400, "hours": 24},
            {"domain": "ngfts.lge.com", "n": 300, "hours": 12}, {"domain": "metrics.icloud.com", "n": 20, "hours": 3},
            {"domain": "www.bbc.co.uk", "n": 60, "hours": 4}, {"domain": "already.example", "n": 999, "hours": 24}]  # fmt: skip
    out = suggest.for_device(rows, {"ngfts.lge.com": ["LG webOS"]}, 7, blocked_here={"already.example"})
    names = [x["domain"] for x in out["suggested"]]
    assert names == ["lgtvsdp.com", "ngfts.lge.com", "metrics.icloud.com"]
    tv = out["suggested"][0]
    assert "around the clock" in tv["why"][0] and tv["may_break"] == "the LG TV's app store and some apps"
    assert out["suggested"][1]["why"] == ["on LG webOS's tracker list"] and out["suggested"][2]["why"] == ["named for metrics"]
    assert [x["domain"] for x in out["keep"]] == ["time.google.com"]
    assert suggest.base("a.b.example.co.uk") == "example.co.uk" and suggest.base("x.lge.com") == "lge.com"


class FakePihole:
    """Pi-hole v6's API, enough of it: auth, groups, clients, exact deny entries."""

    def __init__(self) -> None:
        self.groups = [{"id": 0, "name": "Default"}, {"id": 3, "name": "IoT"}, {"id": 4, "name": "Kids"}]
        self.clients = [{"client": "AA:BB:CC:DD:EE:FF", "groups": [0, 3], "comment": "TV"}]
        self.deny: dict[str, list[int]] = {}
        self.calls: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        p, m = req.url.path, req.method
        self.calls.append(f"{m} {p}")
        body = json.loads(req.content) if req.content else {}
        if p == "/api/auth":
            return httpx.Response(200, json={"session": {"valid": True, "sid": "s1"}}) if m == "POST" else httpx.Response(204)
        assert req.headers.get("x-ftl-sid") == "s1"
        if p == "/api/groups" and m == "GET":
            return httpx.Response(200, json={"groups": self.groups})
        if p == "/api/groups" and m == "POST":
            self.groups.append({"id": 10, "name": body["name"]})
            return httpx.Response(201, json={})
        if p.startswith("/api/groups/") and m == "DELETE":
            self.groups = [g for g in self.groups if g["name"] != p.rsplit("/", 1)[1]]
            return httpx.Response(204)
        if p == "/api/clients" and m == "GET":
            return httpx.Response(200, json={"clients": self.clients})
        if p.startswith("/api/clients/") and m == "PUT":
            c = next(c for c in self.clients if c["client"] == httpx.URL(str(req.url)).path.rsplit("/", 1)[1].replace("%3A", ":"))
            c["groups"] = body["groups"]
            return httpx.Response(200, json={})
        if p == "/api/domains/deny/exact" and m == "POST":
            self.deny[body["domain"]] = body["groups"]
            return httpx.Response(201, json={})
        if p.startswith("/api/domains/deny/exact/") and m == "DELETE":
            self.deny.pop(p.rsplit("/", 1)[1], None)
            return httpx.Response(204)
        if p == "/api/queries":
            return httpx.Response(200, json={"queries": [{"id": 1, "time": NOW.timestamp(), "domain": "a.example", "status": "FORWARDED",
                                                          "client": {"ip": "10.0.2.50"}}]})  # fmt: skip
        return httpx.Response(404)


def test_blocking_keeps_your_groups_and_undo_takes_back_only_what_was_added() -> None:
    fake = FakePihole()
    tv = {"mac": "aa:bb:cc:dd:ee:ff", "ip": "10.0.2.50", "name": "Living-Room-TV", "label": "Living Room TV"}

    async def go() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as c, pihole.Pihole("http://10.0.2.29:8080", "pw", c) as ph:
            assert pihole.groups_of(await ph.clients(), await ph.groups(), tv) == ["IoT"]
            done = await ph.block(tv, ["lgtvsdp.com", "ngfts.lge.com"])
            assert done["group"] == "wa-living-room-tv" and fake.clients[0]["groups"] == [0, 3, 10]  # still in IoT
            assert fake.deny == {"lgtvsdp.com": [10], "ngfts.lge.com": [10]}
            await ph.block(tv, ["metrics.example"], group_id=3)  # for every IoT device
            assert fake.deny["metrics.example"] == [3]
            await ph.unblock(["lgtvsdp.com", "ngfts.lge.com"], tv, all_gone=True)
            assert fake.clients[0]["groups"] == [0, 3] and "wa-living-room-tv" not in [g["name"] for g in fake.groups]
            assert list(fake.deny) == ["metrics.example"]
            assert len(await ph.queries(NOW.timestamp() - 60, NOW.timestamp())) == 1
        assert fake.calls[-1] == "DELETE /api/auth"  # the session given back

    asyncio.run(go())


@pytest.mark.skipif(not URL, reason="needs WA_TEST_DATABASE_URL")
def test_events_are_kept_against_the_right_device() -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.modules.network import store

    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS net_block, net_reference, net_day, net_event, net_device, app_setting"))
        conn.execute(sa.text("CREATE TABLE app_setting (key text primary key, value jsonb, updated_at timestamptz)"))
        mig = importlib.import_module("migrations.versions.0058_network")
        mig.op = Operations(MigrationContext.configure(conn))  # type: ignore[attr-defined]
        mig.upgrade()

    async def go() -> tuple[list[str], list[str], Any, Any]:
        engine = create_async_engine(URL)  # type: ignore[arg-type]
        try:
            async with engine.begin() as conn:
                first = await store.keep(conn, [parse.line(DHCP, NOW), parse.line(FW, NOW)])
            async with engine.begin() as conn:  # Pi-hole names only the address: put against the device it was given to
                again = await store.keep(conn, [parse.pihole({"id": 1, "time": NOW.timestamp(), "domain": "lgtvsdp.com",
                                                              "status": "FORWARDED", "client": {"ip": "10.0.2.50"}})])  # fmt: skip
            async with engine.connect() as conn:
                dev = (await conn.execute(sa.text("SELECT * FROM net_device"))).mappings().all()
                day = (await conn.execute(sa.text("SELECT kind, action, n FROM net_day ORDER BY kind"))).all()
                dns = (await conn.execute(sa.text("SELECT mac FROM net_event WHERE kind = 'dns'"))).scalar()
            return first, again, dev, (day, dns)
        finally:
            await engine.dispose()

    try:
        first, again, dev, (day, dns) = asyncio.run(go())
    finally:
        with sync.begin() as conn:
            conn.execute(sa.text("DROP TABLE IF EXISTS net_block, net_reference, net_day, net_event, net_device, app_setting"))
        sync.dispose()
    assert first == ["aa:bb:cc:dd:ee:ff"] and again == []  # new once
    assert len(dev) == 1 and dev[0]["ip"] == "10.0.2.50" and dev[0]["name"] == "Living-Room-TV"
    assert dns == "aa:bb:cc:dd:ee:ff"
    assert [tuple(r) for r in day] == [("dhcp", "lease", 1), ("dns", "allow", 1), ("firewall", "block", 1)]
