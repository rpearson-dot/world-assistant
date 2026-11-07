"""Next buses at stops near a point: BODS positions along each line's timetable, outside London; TfL in London."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Self
from zoneinfo import ZoneInfo

import pytest

from app.integrations.bods.client import BodsError
from app.integrations.bods.txc import Pattern, Route
from app.integrations.bus import departures as D

LONDON = ZoneInfo("Europe/London")
# a straight road north: stops 1 km apart, A at the bottom
STOPS = {"S-A": (51.300, -0.27), "S-B": (51.309, -0.27), "S-C": (51.318, -0.27), "S-D": (51.327, -0.27)}
ROUTE = Route(line="406", names={"S-A": "Epsom", "S-B": "Ewell", "S-C": "Tolworth", "S-D": "Kingston"},
              patterns={"out": Pattern("outbound", ["S-A", "S-B", "S-C", "S-D"], [0, 240, 480, 720]),
                        "in": Pattern("inbound", ["S-D", "S-C", "S-B", "S-A"], [0, 240, 480, 720])},
              journeys=[("out", 17 * 60 + 30), ("out", 18 * 60), ("in", 17 * 60 + 45)])  # fmt: skip


class Conn:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None


class Engine:
    def connect(self) -> Conn:
        return Conn()


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    D.forget()
    calls: list[tuple[str, str]] = []

    async def load_route(key: str, line: str, noc: str | None, box: Any, check: Any) -> tuple[Route, str]:
        calls.append((str(noc), line))
        if line == "E16":
            raise BodsError("BODS has no timetable for line 'E16'")
        return ROUTE, str(noc)

    async def stop_coords(conn: Any, sids: list[str]) -> dict[str, tuple[float, float]]:
        return {s: STOPS[s] for s in sids if s in STOPS}

    monkeypatch.setattr(D.B, "load_route", load_route)
    monkeypatch.setattr(D.store, "stop_coords", stop_coords)
    return {"calls": calls}


def ts(h: int, m: int, day: int = 9) -> float:  # 9 November 2026 is a Monday
    return datetime(2026, 11, day, h, m, tzinfo=LONDON).timestamp()


async def test_buses_on_their_way_and_the_timetable_after(setup: dict[str, Any]) -> None:
    now = ts(17, 20)
    iso = datetime.fromtimestamp(now - 20, LONDON).isoformat()
    vehicles = [
        {"vehicle": "bus1", "line": "406", "operator": "LGEN", "destination": "Kingston", "bearing": 0.0,
         "lat": 51.3045, "lon": -0.27, "recorded_at": iso},  # half-way from Epsom to Ewell, heading north
        {"vehicle": "bus9", "line": "E16", "operator": "LGEN", "destination": "x", "lat": 51.30, "lon": -0.27, "recorded_at": iso},
    ]  # fmt: skip
    stops = [{"id": "S-C", "name": "Tolworth", "km": 0.1}, {"id": "S-B", "name": "Ewell", "km": 0.2}]
    d = await D.departures(Engine(), "k", 51.318, -0.27, stops, now_ts=now, vehicles=vehicles)
    tol = d["stops"][0]["buses"]
    # bus1 is 1.5 km short of Tolworth at about 16 km/h: 6 minutes; the other way, only the timetable
    assert tol[0] == {"line": "406", "towards": "Kingston", "minutes": 6, "live": True, "vehicle": "bus1"}
    assert tol[1] == {"line": "406", "towards": "Epsom", "at": ["17:49"], "live": False}
    ewell = d["stops"][1]["buses"]
    assert ewell[0]["live"] and ewell[0]["minutes"] == 2
    assert d["no_timetable"] == [{"line": "E16", "operator": "LGEN", "why": "BODS has no timetable for line 'E16'"}]
    assert sorted(setup["calls"]) == [("LGEN", "406"), ("LGEN", "E16")]
    await D.departures(Engine(), "k", 51.318, -0.27, stops, now_ts=now, vehicles=vehicles)
    assert len(setup["calls"]) == 2  # kept: not fetched again


async def test_weekends_use_live_buses_only(setup: dict[str, Any]) -> None:
    d = await D.departures(Engine(), "k", 51.318, -0.27, [{"id": "S-C", "name": "Tolworth", "km": 0.1}],
                           now_ts=ts(17, 20, day=7), vehicles=[{"vehicle": "v", "line": "406", "operator": "LGEN",
                                                                "lat": 51.0, "lon": 0.0}])  # fmt: skip
    assert d["stops"][0]["buses"] == []  # Saturday: the weekday timetable is not used, and no bus is on its way


async def test_a_slow_timetable_does_not_hold_the_answer(setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    gate = asyncio.Event()

    async def slow(key: str, line: str, noc: str | None, box: Any, check: Any) -> tuple[Route, str]:
        await gate.wait()
        return ROUTE, str(noc)

    monkeypatch.setattr(D.B, "load_route", slow)
    monkeypatch.setattr(D, "WAIT_S", 0.05)
    v = [{"vehicle": "v", "line": "406", "operator": "LGEN", "lat": 51.3, "lon": -0.27}]
    d = await D.departures(Engine(), "k", 51.318, -0.27, [{"id": "S-C", "name": "Tolworth", "km": 0.1}], now_ts=ts(17, 20), vehicles=v)
    assert d["loading"] == ["406"] and d["stops"][0]["buses"] == []
    gate.set()
    await asyncio.sleep(0.01)
    d = await D.departures(Engine(), "k", 51.318, -0.27, [{"id": "S-C", "name": "Tolworth", "km": 0.1}], now_ts=ts(17, 20), vehicles=v)
    assert d["loading"] == [] and d["stops"][0]["buses"]  # there on the next ask


async def test_london_stops_use_tfl_countdowns(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import app.integrations.tfl.client as tfl
    from app import bods

    asked: list[str] = []

    class FakeTfl:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        async def get(self, path: str) -> Any:
            asked.append(path)
            return [{"lineName": "213", "destinationName": "Sutton", "timeToStation": 400, "vehicleId": "LX1"},
                    {"lineName": "131", "destinationName": "Tooting", "timeToStation": 70, "vehicleId": "LX2"}]  # fmt: skip

    monkeypatch.setattr(tfl, "TflClient", FakeTfl)
    import app.ratelimit as rl

    monkeypatch.setattr(rl, "SlidingWindowLimiter", lambda *a, **kw: None)
    req: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None)))
    got = await bods._tfl_departures(req, [{"id": "490001234X", "name": "New Malden", "km": 0.1}])
    assert asked == ["/StopPoint/490001234X/Arrivals"]
    assert [(b["line"], b["minutes"]) for b in got[0]["buses"]] == [("131", 1), ("213", 7)]
