"""Notifications through ntfy (faked): settings, quiet hours, sent once, and what after a Garmin sync and the
environment check send."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from app import notify as N
from tests.test_bikes import DB, garage  # noqa: F401 - the fixture


def test_quiet_hours_across_midnight() -> None:
    st = {"quiet_from": "22:00", "quiet_to": "07:00"}
    late = datetime(2026, 11, 8, 23, 30, tzinfo=UTC)  # 23:30 in London (GMT in November)
    assert N.quiet_until(st, late) == datetime(2026, 11, 9, 7, 0, tzinfo=late.astimezone(N.ZoneInfo("Europe/London")).tzinfo)
    assert N.quiet_until(st, datetime(2026, 11, 9, 6, 0, tzinfo=UTC)).hour == 7  # type: ignore[union-attr]
    assert N.quiet_until(st, datetime(2026, 11, 9, 12, 0, tzinfo=UTC)) is None
    assert N.quiet_until({"quiet_from": "13:00", "quiet_to": "14:00"}, datetime(2026, 11, 9, 13, 30, tzinfo=UTC)).hour == 14  # type: ignore[union-attr]
    assert N.quiet_until({"quiet_from": "00:00", "quiet_to": "00:00"}, late) is None
    assert N._ascii("🔋 Battery low") == " Battery low"


@pytest.fixture
def tables(garage: Any) -> Any:  # noqa: F811
    import sqlalchemy as sa

    eng = sa.create_engine(DB)  # type: ignore[arg-type]
    with eng.begin() as c:
        c.execute(sa.text("DROP TABLE IF EXISTS notify_log"))
        c.execute(sa.text("CREATE TABLE IF NOT EXISTS app_setting (key text primary key, value jsonb not null, updated_at timestamptz not null default now())"))
        c.execute(sa.text("DELETE FROM app_setting WHERE key = 'notify'"))
        c.execute(sa.text("ALTER TABLE place ADD COLUMN IF NOT EXISTS name text"))
        c.execute(sa.text("UPDATE place SET name = 'home'"))
        c.execute(sa.text("CREATE TABLE notify_log (id bigserial primary key, key text not null unique, kind text not null, title text not null, "
                          "body text, sent_at timestamptz not null default now(), ok boolean not null, error text)"))  # fmt: skip
    yield garage
    with eng.begin() as c:
        c.execute(sa.text("DROP TABLE IF EXISTS notify_log"))
        c.execute(sa.text("DELETE FROM app_setting WHERE key = 'notify'"))
    eng.dispose()


class Ntfy:
    def __init__(self) -> None:
        self.got: list[dict[str, Any]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.got.append({"path": req.url.path, "headers": dict(req.headers), "body": req.content.decode()})
        return httpx.Response(200, json={"id": "x"})


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_settings_send_once_and_quiet_hours(tables: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app

    c = tables
    monkeypatch.setattr(N, "get_settings", lambda: type("S", (), {"ntfy_url": "http://ntfy:80", "ntfy_public_url": "https://wa.tail1.ts.net:8443",
                                                                   "local_tz": "Europe/London"})())  # fmt: skip
    s = c.get("/v1/notify/settings").json()
    assert s["topic"].startswith("wa-") and len(s["topic"]) > 20 and s["kinds"] == {"bikes": True, "firmware": False, "environment": True, "home": True, "health": True, "system": False}
    assert s["subscribe"] == f"https://wa.tail1.ts.net:8443/{s['topic']}" and c.get("/v1/notify/settings").json()["topic"] == s["topic"]
    assert c.put("/v1/notify/settings", json={"kinds": {"nonsense": True}}).status_code == 422
    assert c.put("/v1/notify/settings", json={"quiet_from": "25:00"}).status_code == 422
    c.put("/v1/notify/settings", json={"kinds": {"firmware": True}, "quiet_from": "21:00"})
    fake = Ntfy()
    eng = app.state.engine

    async def go(**kw: Any) -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as cl:
            return await N.send(eng, kw.pop("kind", "bikes"), kw.pop("key", "k1"), "🔋 Battery low", "Heart rate strap: low", client=cl, **kw)

    noon = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    assert asyncio.run(go(now=noon))["sent"] and fake.got[0]["path"] == f"/{s['topic']}" and fake.got[0]["headers"]["title"] == " Battery low"
    assert "delay" not in fake.got[0]["headers"] and fake.got[0]["body"] == "Heart rate strap: low"
    assert asyncio.run(go(now=noon)) == {"sent": False, "why": "sent before"} and len(fake.got) == 1
    night = datetime(2026, 11, 9, 23, 0, tzinfo=UTC)
    r = asyncio.run(go(key="k2", now=night))
    assert r["held_until"].startswith("2026-11-10T07:00") and fake.got[1]["headers"]["delay"] == str(int(datetime(2026, 11, 10, 7, tzinfo=UTC).timestamp()))
    assert "delay" not in asyncio.run(go(key="k3", now=night, urgent=True)) or fake.got[2]["headers"]["priority"] == "5"
    assert asyncio.run(go(key="k4", kind="system", now=noon)) == {"sent": False, "why": "switched off"}
    log = c.get("/v1/notify/log").json()["sent"]
    assert [x["key"] for x in log][:1] and all(x["ok"] for x in log) and len(log) == 3
    assert c.put("/v1/notify/settings", json={"new_topic": True}).json()["topic"] != s["topic"]


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_after_a_sync_low_batteries_due_parts_and_firmware_in_one_message_each(tables: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlalchemy as sa

    from app.main import app
    from app.modules.bikes import api as A
    from app.modules.bikes import notify as J

    c = tables
    sent: list[tuple[str, str, str, str]] = []

    async def fake_send(engine: Any, kind: str, key: str, title: str, body: str, **kw: Any) -> dict[str, Any]:
        sent.append((kind, key, title, body))
        async with engine.begin() as cn:
            await cn.execute(sa.text("INSERT INTO notify_log (key, kind, title, body, ok) VALUES (:k, :kind, :t, :b, true)"),
                             {"k": key, "kind": kind, "t": title, "b": body})  # fmt: skip
        return {"sent": True}

    monkeypatch.setattr(J.notify, "send", fake_send)
    c.put("/v1/notify/settings", json={"kinds": {"firmware": True}})
    c.post("/v1/bikes/riders", json={"name": "Rob", "weight_kg": 80})
    bike = c.post("/v1/bikes/bikes", json={"rider": "rob", "label": "Reiver", "type": "gravel", "fitted_at": "2026-01-01"}).json()["id"]
    c.post("/v1/bikes/rides", json={"rider": "rob", "bike_id": bike, "started": "2026-02-01T09:00", "km": 6000, "hours": 250, "wet": False})
    now = datetime.now(UTC)
    eng = app.state.engine
    readings = [{"maker": "Garmin", "type": "heart rate", "serial": 22, "status": "low", "level": 20, "at": now.isoformat(), "activity": "Running"},
                {"maker": "Garmin", "type": "speed", "serial": 11, "status": "good", "level": 80, "at": now.isoformat(), "bike_id": bike}]  # fmt: skip
    asyncio.run(A.save_sensors(eng, "rob", readings))
    fw = [{"serial": 5, "maker": "SRAM", "type": "shifting", "name": None, "software": 1.3, "was": 1.2}]
    out = asyncio.run(J.after_sync(eng, "rob", fw))
    assert out["low"] == 1 and out["due"] >= 3 and out["firmware"] == 1 and "sensors" not in out  # the first: sensors only remembered
    titles = [t for _, _, t, _ in sent]
    assert "Rob: battery low" in titles and f"Rob: {out['due']} parts due" in titles and "Firmware updated" in titles
    due_body = next(b for _, _, t, b in sent if t.endswith("parts due"))
    assert "on Reiver" in due_body and len(due_body.splitlines()) == out["due"]
    assert next(b for _, _, t, b in sent if t == "Firmware updated") == "SRAM shifting: 1.2 → 1.3"
    sent.clear()
    assert asyncio.run(J.after_sync(eng, "rob", fw)) == {"low": 0, "due": 0, "sensors": 0, "firmware": 0} and sent == []  # said once
    # a new sensor after the first sync is said
    asyncio.run(A.save_sensors(eng, "rob", [{**readings[1], "serial": 99, "type": "radar", "status": "new", "level": 100}]))
    assert asyncio.run(J.after_sync(eng, "rob"))["sensors"] == 1 and sent[-1][2] == "New sensor seen"


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_environment_check_high_air_and_very_high_pollen(tables: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app
    from app.modules.environment import notify as J
    from app.modules.environment.sources.environment import clients as C
    from app.modules.pollen import pollen_grid

    sent: list[tuple[str, str]] = []

    async def fake_send(engine: Any, kind: str, key: str, title: str, body: str, **kw: Any) -> dict[str, Any]:
        sent.append((key, title))
        return {"sent": True}

    async def air(client: Any, lat: float, lon: float, km: float = 20) -> dict[str, Any]:
        return {"index": 8, "band": "High", "worst": "Ozone"}

    async def grid(engine: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(J.notify, "send", fake_send)
    monkeypatch.setattr(C, "air_near", air)
    monkeypatch.setattr(pollen_grid, "load", grid)
    monkeypatch.setattr(pollen_grid, "current", lambda pts: pts)
    monkeypatch.setattr(pollen_grid, "nearest", lambda pts, la, lo: ({"forecast": {"allergens": [{"name": "Grasses", "levels": [4, 3]},
                                                                                                    {"name": "Birch", "levels": [1]}]}}, 3))  # fmt: skip
    out = asyncio.run(J.environment_check(app.state.engine, client=object(), today=date(2026, 6, 1)))  # type: ignore[arg-type]
    assert out["air"] == 1 and out["pollen"] == 1
    assert ("air:home:2026-06-01", "Air pollution high at home") in sent and ("pollen:home:2026-06-01", "Very high pollen today") in sent


def test_the_environment_pages_and_their_tabs() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    for path in ("/environment", "/environment/floods", "/environment/tides", "/environment/overflows"):
        r = c.get(path)
        assert r.status_code == 200 and 'aria-label="Environment"' in r.text and 'href="/pollen"' in r.text
    assert c.get("/environment/nope").status_code == 404
    assert 'href="/environment"' in c.get("/pollen").text  # Pollen sits under the same tabs
