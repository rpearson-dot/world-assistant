from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Self
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from app.integrations.bus.config import TrackerConfig
from app.integrations.bus.poller import Lookup, in_window, poll_one, process_poll
from app.integrations.bus.trips import new_state
from app.main import app

TZ = ZoneInfo("Europe/London")
DAY = date(2026, 10, 20)  # a Tuesday, BST
T0 = datetime(2026, 10, 20, 6, 0, tzinfo=UTC).timestamp()  # 07:00 local


def cfg_dict(**over: Any) -> dict[str, Any]:
    base = {
        "line": "213",
        "stops": [
            {"key": "start", "sid": "AAAA0001", "label": "Start stop"},
            {"key": "mid", "sid": "AAAA0002", "label": "Middle stop"},
            {"key": "dest", "sid": "AAAA0003", "label": "Destination stop"},
            {"key": "dest_back", "sid": "AAAA0004", "label": "Destination, return side"},
        ],
        "target": {"board": "start", "alight": "dest", "alight_return": "dest_back", "before_alight": "mid"},
        "earliest": "07:00", "deadline": "08:10", "min_travel_min": 12,
    }  # fmt: skip
    return {**base, **over}


SEQ = [
    {"direction": "outbound", "pos": 0, "sid": "AAAA0001", "name": "Start stop"},
    {"direction": "outbound", "pos": 1, "sid": "AAAA0002", "name": "Middle stop"},
    {"direction": "outbound", "pos": 2, "sid": "AAAA0003", "name": "Destination stop"},
    {"direction": "outbound", "pos": 3, "sid": "AAAA0009", "name": "Further on"},
    {"direction": "inbound", "pos": 0, "sid": "AAAA0004", "name": "Destination, return side"},
]


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


def feed(vehicle: str, arrivals: dict[str, float], now: float) -> list[dict[str, Any]]:
    return [{"vehicleId": vehicle, "naptanId": s, "timeToStation": max(int(t - now), 0), "expectedArrival": iso(t)}
            for s, t in arrivals.items() if t > now - 5]  # fmt: skip


def test_config_validation() -> None:
    cfg = TrackerConfig.model_validate(cfg_dict())
    assert cfg.segment_pairs() == [
        ("AAAA0001", "AAAA0002"),
        ("AAAA0002", "AAAA0003"),
        ("AAAA0003", "AAAA0004"),
    ]
    assert cfg.target_cfg()["alight_return"] == "AAAA0004"  # type: ignore[index]
    with pytest.raises(ValidationError):
        TrackerConfig.model_validate(cfg_dict(target={"board": "start", "alight": "nowhere"}))
    with pytest.raises(ValidationError):
        TrackerConfig.model_validate(cfg_dict(deadline="25:00"))
    with pytest.raises(ValidationError):
        TrackerConfig.model_validate(
            cfg_dict(stops=[{"key": "a", "sid": "AAAA0001", "label": "x"}] * 2)
        )


def test_window_is_weekdays_only() -> None:
    cfg = TrackerConfig.model_validate(cfg_dict())
    assert in_window(cfg, datetime(2026, 10, 20, 7, 30, tzinfo=TZ))
    assert not in_window(cfg, datetime(2026, 10, 20, 10, 0, tzinfo=TZ))
    assert not in_window(cfg, datetime(2026, 10, 24, 7, 30, tzinfo=TZ))  # Saturday


def drive(arrivals: dict[str, float], start: float, end: float, vehicle: str = "V1",
          direction_feed: bool = True) -> list[dict[str, Any]]:  # fmt: skip
    cfg = TrackerConfig.model_validate(cfg_dict())
    state, lookup, hits = new_state(), Lookup(SEQ), []
    now = start
    while now <= end:
        out = process_poll(
            cfg, state, feed(vehicle, arrivals, now), lookup, now, DAY, TZ, ["07:30", "08:00"]
        )
        hits += out["hits"]
        now += 30
    return hits


def test_bus_before_deadline_is_a_hit_with_its_slot() -> None:
    board = T0 + 30 * 60  # 07:30 local
    hits = drive({"AAAA0001": board, "AAAA0002": board + 600, "AAAA0003": board + 900, "AAAA0009": board + 1200},
                 board - 600, board + 1400)  # fmt: skip
    assert len(hits) == 1
    assert hits[0]["slot"] == "07:30" and hits[0]["delay_min"] == 0 and not hits[0]["via_return"]


def test_bus_after_deadline_is_not_a_hit() -> None:
    board = T0 + 65 * 60  # 08:05 local; arrives 08:20, after the 08:10 deadline
    hits = drive({"AAAA0001": board, "AAAA0002": board + 600, "AAAA0003": board + 900, "AAAA0009": board + 1200},
                 board - 600, board + 1400)  # fmt: skip
    assert hits == []


def test_bus_terminating_at_destination_counts_via_return_side() -> None:
    board = T0 + 30 * 60
    hits = drive(
        {"AAAA0001": board, "AAAA0002": board + 600, "AAAA0004": board + 800},
        board - 600,
        board + 1000,
    )
    assert len(hits) == 1 and hits[0]["via_return"]


# ---- database + API -----------------------------------------------------------------------

URL = os.environ.get("WA_TEST_DATABASE_URL")
MIGRATION = Path(__file__).parent.parent / "migrations" / "versions" / "0011_bus_tracking.py"


@pytest.fixture
def schema() -> Iterator[None]:
    spec = importlib.util.spec_from_file_location("m0011", MIGRATION)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    eng = sa.create_engine(URL)  # type: ignore[arg-type]
    with eng.begin() as conn:
        for t in ("bus_timetable", "bus_target_hit", "bus_target_day", "bus_anomaly", "bus_segment_obs",
                  "bus_stop", "bus_tracker"):  # fmt: skip
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        with Operations.context(MigrationContext.configure(conn)):
            m.upgrade()
    yield
    with eng.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        m.downgrade()
    eng.dispose()


class _FakeClient:
    board = T0 + 30 * 60

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def route_sequence(self, line: str, direction: str) -> list[dict[str, str]]:
        return [{"id": r["sid"], "name": r["name"]} for r in SEQ if r["direction"] == direction]

    async def timetable_times(self, line: str, stop: str, direction: str) -> list[str]:
        return ["07:00", "07:30", "08:00", "09:00"]

    async def line_arrivals(self, line: str) -> list[dict[str, Any]]:
        return feed("V9", {"AAAA0001": self.board, "AAAA0002": self.board + 600, "AAAA0003": self.board + 900,
                           "AAAA0009": self.board + 1200}, self.now)  # fmt: skip

    now = 0.0


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_poll_stores_hit_and_api_reports_it(
    schema: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        app.state.engine = engine
        c = TestClient(app)
        h = {"Authorization": "Bearer tok"}
        assert c.put("/v1/bus/trackers/main", json={"config": {**cfg_dict(), "target": {"board": "zzz", "alight": "dest"}}},
                     headers=h).status_code == 422  # fmt: skip
        assert (
            c.put("/v1/bus/trackers/main", json={"config": cfg_dict()}, headers=h).status_code
            == 200
        )
        assert c.get("/v1/bus/trackers/nope", headers=h).status_code == 404
        client = _FakeClient()
        now = _FakeClient.board - 600
        while now <= _FakeClient.board + 1500:
            client.now = now
            async with engine.connect() as conn:
                from app.integrations.bus import store

                row = await store.get_tracker(conn, "main")
            assert row
            await poll_one(engine, client, row, now)  # type: ignore[arg-type]
            now += 30
        t = c.get("/v1/bus/trackers/main/target?day=2026-10-20", headers=h).json()
        assert t["scheduled"] == ["07:00", "07:30", "08:00"] or t["scheduled"] == ["07:00", "07:30"]
        assert [x["vehicle"] for x in t["hits"]] == ["V9"] and t["hits"][0]["slot"] == "07:30"
        assert "reached the target stop" in t["summary"]
        assert t["pending"] and "07:30" not in t["pending"]
        r = c.get("/v1/bus/trackers/main/reliability", headers=h).json()
        assert r["days_recorded"] == 1
        cond = c.get("/v1/bus/trackers/main/conditions", headers=h).json()
        assert len(cond) == 3
        assert c.get("/v1/bus/trackers/main/anomalies", headers=h).status_code == 200
        assert c.post("/v1/bus/trackers/main/refresh-route", headers=h).status_code == 200
        assert c.delete("/v1/bus/trackers/main", headers=h).status_code == 204
    finally:
        await engine.dispose()


def test_turnaround_at_a_place_is_captured_once() -> None:
    """A bus that goes outbound past a place, turns, and comes back inbound past it."""
    stops = [
        {"key": "p_out", "sid": "AAAA0002", "label": "Place, outbound", "place": "place"},
        {"key": "p_in", "sid": "AAAA0005", "label": "Place, inbound", "place": "place"},
    ]
    cfg = TrackerConfig.model_validate({"line": "213", "stops": stops})
    seq = [
        {"direction": "outbound", "pos": 0, "sid": "AAAA0001", "name": ""},
        {"direction": "outbound", "pos": 1, "sid": "AAAA0002", "name": ""},
        {"direction": "outbound", "pos": 2, "sid": "AAAA0009", "name": ""},
        {"direction": "inbound", "pos": 0, "sid": "AAAA0008", "name": ""},
        {"direction": "inbound", "pos": 1, "sid": "AAAA0005", "name": ""},
        {"direction": "inbound", "pos": 2, "sid": "AAAA0007", "name": ""},
    ]
    state, lookup, found = new_state(), Lookup(seq), []
    arrivals = {"AAAA0001": T0, "AAAA0002": T0 + 300, "AAAA0005": T0 + 700,
                "AAAA0007": T0 + 900}  # fmt: skip
    now = T0 - 60
    while now <= T0 + 1000:
        out = process_poll(cfg, state, feed("V3", arrivals, now), lookup, now, DAY, TZ, [])
        found += [a for a in out["anomalies"] if a["type"] == "turnaround"]
        now += 30
    assert len(found) == 1
    t = found[0]
    assert t["place"] == "place" and (t["from_dir"], t["to_dir"]) == ("outbound", "inbound")
    assert t["from_sid"] == "AAAA0002" and t["to_sid"] == "AAAA0005" and t["gap_s"] == 400


def test_bus_page_is_served_without_a_token() -> None:
    r = TestClient(app).get("/bus")
    assert r.status_code == 200 and "Buses" in r.text and "innerHTML" not in r.text


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_backfill_records_a_missed_run_once_from_the_stored_state(
    schema: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import text

    from app.integrations.bus import poller, store
    from app.integrations.bus.backfill import backfill

    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        async with engine.begin() as conn:
            await store.upsert_tracker(conn, "main", cfg_dict(), True)
        monkeypatch.setattr(poller, "compute_hits", lambda *a, **k: [])  # as if live matching had missed the bus
        client = _FakeClient()
        now = _FakeClient.board - 600
        while now <= _FakeClient.board + 1500:
            client.now = now
            async with engine.connect() as conn:
                row = await store.get_tracker(conn, "main")
            assert row
            await poll_one(engine, client, row, now)  # type: ignore[arg-type]
            now += 30
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM bus_target_hit"))).scalar_one() == 0
        monkeypatch.undo()
        out = await backfill(engine, "main", DAY, now=_FakeClient.board + 2000)
        assert out["ok"] is True and out["found"] == 1 and out["buses"][0]["vehicle"] == "V9" and out["buses"][0]["slot"] == "07:30"
        again = await backfill(engine, "main", DAY, now=_FakeClient.board + 2000)
        assert again["found"] == 1  # it finds it again, but...
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM bus_target_hit"))).scalar_one() == 1  # ...saves it once
        assert (await backfill(engine, "nope", DAY))["ok"] is False
    finally:
        await engine.dispose()
