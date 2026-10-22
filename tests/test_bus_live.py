"""GPS positions for the buses a tracker follows, and the way-back tracker."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app import secretstore
from app.integrations.bus import live
from app.integrations.bus.config import TrackerConfig
from app.integrations.bus.poller import poll_one
from app.integrations.bus.setup import SetupError, _shift, return_config
from app.main import app
from tests.test_bus_poller import T0, _FakeClient, cfg_dict, iso

URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
TOWARDS = {"outbound": "Kingston Fairfield Bus Station", "inbound": "Sutton"}

# --- the rules, with no database ------------------------------------------------------------------


def test_the_destination_board_says_which_way_a_bus_is_heading() -> None:
    assert live.direction_of("Fairfield Bus Station", TOWARDS) == "outbound"
    assert live.direction_of("Sutton Bus Garage", TOWARDS) == "inbound"
    assert live.direction_of("Nowhere in particular", TOWARDS) is None
    assert live.direction_of(None, TOWARDS) is None
    assert live.direction_of("Bus Station", TOWARDS) is None  # only filler words in common


def test_positions_are_wanted_only_inside_the_journey_window() -> None:
    cfg = TrackerConfig.model_validate(cfg_dict())  # 07:00 to 08:10
    assert [live.in_journey_window(cfg, m) for m in (6 * 60 + 59, 7 * 60, 8 * 60 + 10, 8 * 60 + 11)] == [False, True, True, False]


def test_only_buses_heading_for_the_trackers_stops_are_known() -> None:
    preds = [{"vehicleId": "A", "naptanId": "AAAA0002"}, {"vehicleId": "B", "naptanId": "ZZZZ9999"}, {"naptanId": "AAAA0002"}]
    state = {"trips": {"C": {"stops": [{"sid": "AAAA0001"}]}, "D": {"stops": [{"sid": "ZZZZ9999"}]}, "E": {"stops": []}}}
    assert live.known_vehicles(state, preds, {"AAAA0001", "AAAA0002"}) == {"A", "C"}


def test_gps_is_added_to_events_and_marks_a_prediction_shift() -> None:
    fix = {"vehicle": "V1", "destination": "Fairfield Bus Station", "bearing": 315.0, "lat": 51.38, "lon": -0.24,
           "recorded_at": "2026-10-22T15:28:01+00:00"}  # fmt: skip
    events = [{"vehicle": "V1", "type": "reverse_movement", "direction": "outbound"},
              {"vehicle": "V1", "type": "reverse_movement", "direction": "inbound"},
              {"vehicle": "V2", "type": "reverse_movement", "direction": "outbound"},
              {"vehicle": "V1", "type": "early_termination"}]  # fmt: skip
    assert live.attach(events, {"V1": fix}, TOWARDS) == 3
    assert events[0]["verdict"] == "prediction_shift" and events[0]["live"]["towards"] == "Kingston Fairfield Bus Station"
    assert events[1]["verdict"] == "check"  # GPS says the other way: worth a look
    assert "live" not in events[2] and "verdict" not in events[2]  # not a bus we can see
    assert "live" in events[3] and "verdict" not in events[3]


def test_the_way_back_swaps_the_sides_and_has_its_own_window() -> None:
    seqs = {
        "outbound": [{"id": "OUT1", "name": "Worcester Park"}, {"id": "OUT2", "name": "Mid"}, {"id": "OUT3", "name": "Coombe Girls School"},
                     {"id": "OUT4", "name": "Kingston"}],
        "inbound": [{"id": "INB1", "name": "Kingston"}, {"id": "INB2", "name": "Coombe Girls School"}, {"id": "INB3", "name": "Mid"},
                    {"id": "INB4", "name": "Worcester Park"}],
    }  # fmt: skip
    back = return_config("213", seqs, "OUT1", "OUT3", "15:00", "17:30")
    by = {s["key"]: s for s in back["stops"]}
    assert (by["board"]["sid"], by["target"]["sid"]) == ("INB2", "INB4")  # Coombe Girls School to Worcester Park, the other side
    assert back["target"]["direction"] == "inbound"
    assert (back["earliest"], back["deadline"], back["poll_start"], back["poll_end"]) == ("15:00", "17:30", "14:30", "18:30")
    TrackerConfig.model_validate(back)
    with pytest.raises(SetupError):
        return_config("213", {**seqs, "inbound": [{"id": "INB9", "name": "Elsewhere"}]}, "OUT1", "OUT3", "15:00", "17:30")
    assert (_shift("00:10", -30), _shift("23:30", 60)) == ("00:00", "23:59")


def test_the_setup_page_offers_the_way_back() -> None:
    page = TestClient(app).get("/bus").text
    assert 'id="back"' in page and "return_earliest" in page and 'value="15:00"' in page and 'value="17:30"' in page


# --- the database ---------------------------------------------------------------------------------

TABLES = ["bus_position", "bus_timetable", "bus_target_hit", "bus_target_day", "bus_anomaly", "bus_segment_obs",
          "bus_stop", "bus_tracker", "naptan_stop"]  # fmt: skip


@pytest.fixture
def schema() -> Iterator[None]:
    eng = sa.create_engine(URL)  # type: ignore[arg-type]
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS postgis"))
        for t in TABLES:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        with Operations.context(MigrationContext.configure(conn)):
            for name in ("0011_bus_tracking", "0020_naptan", "0026_bus_position"):
                spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
                assert spec and spec.loader
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                m.upgrade()
        for i, sid in enumerate(["AAAA0001", "AAAA0002", "AAAA0003", "AAAA0009", "AAAA0004"]):
            conn.execute(sa.text(
                "INSERT INTO naptan_stop (atco, name, stop_type, kind, lat, lon, seen) "
                "VALUES (:a, 'S', 'BCT', 'bus', :lat, :lon, now())"), {"a": sid, "lat": 51.38 + i * 0.004, "lon": -0.24 + i * 0.004})  # fmt: skip
    yield
    with eng.begin() as conn:
        for t in TABLES:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    eng.dispose()


async def drive(engine: Any, start: float, end: float, monkeypatch: pytest.MonkeyPatch, asked: list[float]) -> None:
    from app.integrations.bus import store

    client = _FakeClient()
    now = start
    while now <= end:
        client.now = now
        async with engine.connect() as conn:
            row = await store.get_tracker(conn, "main")
        assert row
        current = now

        async def fake_fetch(key: str, box: Any, _t: float = current) -> list[dict[str, Any]]:
            asked.append(_t)
            base = {"line": "213", "operator": "TFLO", "destination": "Fairfield Bus Station", "direction": "2",
                    "bearing": 315.0, "lat": 51.381, "lon": -0.241, "recorded_at": iso(_t - 5)}  # fmt: skip
            return [{**base, "vehicle": "V9"}, {**base, "vehicle": "OTHER"}, {**base, "vehicle": "V9", "line": "999"}]

        monkeypatch.setattr(live, "fetch_vehicles", fake_fetch)
        await poll_one(engine, client, row, now)  # type: ignore[arg-type]
        now += 30


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_positions_are_gathered_only_in_the_window_and_only_for_followed_buses(
    schema: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    monkeypatch.setenv("BODS_API_KEY", "k")
    secretstore.clear_cache()
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        app.state.engine = engine
        c, h = TestClient(app), {"Authorization": "Bearer tok"}
        assert c.put("/v1/bus/trackers/main", json={"config": cfg_dict()}, headers=h).status_code == 200
        asked: list[float] = []
        board = _FakeClient.board
        await drive(engine, board - 600, board + 600, monkeypatch, asked)  # 07:20 to 07:40, inside 07:00-08:10
        assert asked and len(asked) == len({*asked})
        got = c.get("/v1/bus/trackers/main/positions?day=2026-10-20", headers=h).json()
        assert got["count"] == len(asked) and {f["vehicle"] for f in got["fixes"]} == {"V9"}  # not OTHER, not the wrong line
        assert got["fixes"][0]["destination"] == "Fairfield Bus Station" and got["fixes"][0]["bearing"] == 315.0
        assert c.get("/v1/bus/trackers/main/positions?day=2026-10-20&vehicle=NOPE", headers=h).json()["count"] == 0

        # 06:40 to 06:55 is polled (from 06:30) but is before the earliest bus: nothing is asked of BODS
        before: list[float] = []
        await drive(engine, T0 - 20 * 60, T0 - 5 * 60, monkeypatch, before)
        assert before == []
        # and with no key at all it never asks
        monkeypatch.delenv("BODS_API_KEY")
        secretstore.clear_cache()
        quiet: list[float] = []
        await drive(engine, board + 700, board + 800, monkeypatch, quiet)
        assert quiet == []
    finally:
        await engine.dispose()
        secretstore.clear_cache()
