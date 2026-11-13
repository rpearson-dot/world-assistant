"""The bus route recorder: when it records, reading TfL and BODS, telling new, moved and gone predictions apart, and
(with a database) a recorded minute end to end, failures included."""

from __future__ import annotations

import asyncio
import gzip
import importlib
import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import text

from app.modules.buslog import parse
from app.modules.buslog.schedule import (
    DEFAULT_CALENDAR,
    DEFAULT_SETTINGS,
    check_calendar,
    check_settings,
    recording,
    school_day,
)

LON = ZoneInfo("Europe/London")
URL = os.environ.get("WA_TEST_DATABASE_URL")
CAL = DEFAULT_CALENDAR


# --- when -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("day", "ok", "why"), [
    (date(2026, 11, 16), True, "Autumn term"),
    (date(2026, 11, 14), False, "weekend"),
    (date(2026, 11, 4), False, "INSET day"),
    (date(2026, 10, 28), False, "Autumn half term"),
    (date(2026, 11, 3), False, "Autumn half term"),
    (date(2026, 12, 21), False, "outside term"),
    (date(2027, 2, 17), False, "Spring half term"),
    (date(2027, 7, 2), False, "INSET day"),
    (date(2027, 4, 12), True, "Summer term (School starts at 10:45 (gates open 10:30))"),
])  # fmt: skip
def test_school_days_follow_the_calendar(day: date, ok: bool, why: str) -> None:
    assert school_day(day, CAL) == (ok, why)


def test_a_bank_holiday_is_not_a_school_day() -> None:
    assert school_day(date(2027, 5, 3), CAL, bank_holiday=True) == (False, "bank holiday")


def test_records_in_the_window_on_school_days_and_every_day_while_testing() -> None:
    st = dict(DEFAULT_SETTINGS)
    monday_0710 = datetime(2026, 11, 16, 7, 10, tzinfo=LON)
    assert recording(monday_0710, st, CAL, False)[0]
    assert not recording(datetime(2026, 11, 16, 9, 0, tzinfo=LON), st, CAL, False)[0]
    assert not recording(datetime(2026, 11, 16, 6, 59, tzinfo=LON), st, CAL, False)[0]
    saturday = datetime(2026, 11, 14, 7, 30, tzinfo=LON)
    assert recording(saturday, st, CAL, False) == (False, "weekend")
    assert recording(saturday, {**st, "every_day": True}, CAL, False) == (True, "every day (test)")
    assert not recording(datetime(2026, 11, 14, 12, 0, tzinfo=LON), {**st, "every_day": True}, CAL, False)[0]
    # record now: any time, until it runs out
    noon = datetime(2026, 11, 14, 12, 0, tzinfo=LON)
    assert recording(noon, st, CAL, False, until=noon.timestamp() + 60) == (True, "recording now, as asked")
    assert not recording(noon, st, CAL, False, until=noon.timestamp() - 1)[0]


def test_settings_and_calendar_are_checked() -> None:
    assert check_settings({"line": " 213 ", "start": "7:00", "end": "09:00"})["line"] == "213"
    assert check_settings({"start": "7:5"})["start"] == "07:05"
    with pytest.raises(ValueError):
        check_settings({"start": "09:00", "end": "08:00"})
    with pytest.raises(ValueError):
        check_settings({"line": "2 1 3; drop"})
    with pytest.raises(ValueError):
        check_calendar({"terms": [{"name": "x", "from": "2027-01-02", "to": "2027-01-01"}]})
    assert check_calendar(CAL)["inset"][0] == "2026-09-03"


# --- TfL predictions --------------------------------------------------------------------------------------------


def _arr(vehicle: str, stop: str, expected: str) -> dict[str, Any]:
    return {"vehicleId": vehicle, "naptanId": stop, "stationName": "Stop " + stop, "direction": "outbound",
            "destinationName": "Sutton", "towards": "Worcester Park", "expectedArrival": expected,
            "timeToStation": 120, "timestamp": "2026-11-16T07:10:00Z", "currentLocation": "", "lineId": "213"}  # fmt: skip


def test_predictions_are_recorded_when_new_moved_or_gone() -> None:
    first = parse.arrivals([_arr("LJ1", "A", "2026-11-16T07:15:00Z"), _arr("LJ2", "A", "2026-11-16T07:25:00Z"),
                            {"vehicleId": None}])  # fmt: skip
    rows, state = parse.changes({}, first)
    assert [r["change"] for r in rows] == ["new", "new"] and set(state) == {"LJ1|A", "LJ2|A"}
    # LJ1 slips 10 s (the same prediction), LJ2 slips 2 minutes (moved), and a third bus appears
    second = parse.arrivals([_arr("LJ1", "A", "2026-11-16T07:15:10Z"), _arr("LJ2", "A", "2026-11-16T07:27:00Z"),
                             _arr("LJ3", "B", "2026-11-16T07:40:00Z")])  # fmt: skip
    rows, state = parse.changes(state, second)
    assert {(r["vehicle"], r["change"], r["moved_s"]) for r in rows} == {("LJ2", "moved", 120), ("LJ3", "new", None)}
    # LJ1 is no longer predicted at A: gone, with the time it was last expected
    rows, _ = parse.changes(state, [r for r in second if r["vehicle"] != "LJ1"])
    assert [(r["vehicle"], r["change"]) for r in rows] == [("LJ1", "gone")]
    assert rows[0]["previous_at"] == datetime(2026, 11, 16, 7, 15, 10, tzinfo=UTC)


def test_an_answer_that_is_not_a_list_is_an_error() -> None:
    with pytest.raises(ValueError):
        parse.arrivals({"message": "Invalid app_key"})


# --- BODS ---------------------------------------------------------------------------------------------------------

SIRI = b"""<?xml version="1.0" encoding="UTF-8"?>
<Siri xmlns="http://www.siri.org.uk/siri" version="2.0"><ServiceDelivery><VehicleMonitoringDelivery>
<VehicleActivity><RecordedAtTime>2026-11-16T07:10:03+00:00</RecordedAtTime><ItemIdentifier>x1</ItemIdentifier>
<ValidUntilTime>2026-11-16T07:15:03+00:00</ValidUntilTime>
<MonitoredVehicleJourney><LineRef>213</LineRef><DirectionRef>outbound</DirectionRef>
<FramedVehicleJourneyRef><DataFrameRef>2026-11-16</DataFrameRef><DatedVehicleJourneyRef>1234</DatedVehicleJourneyRef></FramedVehicleJourneyRef>
<PublishedLineName>213</PublishedLineName><OperatorRef>LGEN</OperatorRef><OriginRef>490000001</OriginRef><OriginName>Kingston</OriginName>
<DestinationRef>490000002</DestinationRef><DestinationName>Sutton</DestinationName>
<OriginAimedDepartureTime>2026-11-16T06:55:00+00:00</OriginAimedDepartureTime>
<VehicleLocation><Longitude>-0.2601</Longitude><Latitude>51.3901</Latitude></VehicleLocation><Bearing>120</Bearing>
<Occupancy>seatsAvailable</Occupancy><BlockRef>77</BlockRef><VehicleRef>LJ16EZX</VehicleRef></MonitoredVehicleJourney>
<Extensions><VehicleJourney><DriverRef>5</DriverRef></VehicleJourney></Extensions></VehicleActivity>
<VehicleActivity><RecordedAtTime>2026-11-16T07:10:03+00:00</RecordedAtTime><MonitoredVehicleJourney><LineRef>213</LineRef>
<PublishedLineName>213</PublishedLineName><VehicleRef>OTHER</VehicleRef><OperatorRef>FAR</OperatorRef></MonitoredVehicleJourney></VehicleActivity>
<VehicleActivity><MonitoredVehicleJourney><LineRef>K3</LineRef><VehicleRef>NOTOURS</VehicleRef></MonitoredVehicleJourney></VehicleActivity>
</VehicleMonitoringDelivery></ServiceDelivery></Siri>"""


def test_bods_positions_keep_every_field() -> None:
    rows = parse.positions(SIRI, "213")
    assert [r["vehicle"] for r in rows] == ["LJ16EZX", "OTHER"]
    b = rows[0]
    assert (b["lat"], b["lon"], b["bearing"], b["journey_ref"], b["block_ref"]) == (51.3901, -0.2601, 120.0, "1234", "77")
    assert b["origin_aimed_departure"] == datetime(2026, 11, 16, 6, 55, tzinfo=UTC)
    assert b["fields"]["Extensions.VehicleJourney.DriverRef"] == "5" and b["fields"]["ItemIdentifier"] == "x1"
    assert rows[1]["lat"] is None


def test_bods_answers_that_are_not_siri_are_errors() -> None:
    with pytest.raises(ValueError):
        parse.positions(b"<html><body>Bad gateway", "213")
    with pytest.raises(ValueError):
        parse.positions(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>', "213")


# --- TfL route, timetable, status ---------------------------------------------------------------------------------

ROUTE = {"orderedLineRoutes": [{"naptanIds": ["490A", "490B", "490C"]}],
         "stopPointSequences": [{"stopPoint": [{"id": "490A", "name": "Kingston", "lat": 51.41, "lon": -0.30, "stopLetter": "K"},
                                               {"id": "490B", "name": "New Malden", "lat": 51.40, "lon": -0.26},
                                               {"id": "490C", "name": "Sutton", "lat": 51.36, "lon": -0.19}]}],
         "lineStrings": ["[[[-0.30,51.41],[-0.26,51.40],[-0.19,51.36]]]"]}  # fmt: skip


def test_the_route_prefers_naptan_positions() -> None:
    r = parse.route(ROUTE, {"490B": (51.4011, -0.2622, "New Malden Fountain")})
    assert [s["stop"] for s in r["stops"]] == ["490A", "490B", "490C"]
    assert r["stops"][1] == {"seq": 1, "stop": "490B", "name": "New Malden Fountain", "lat": 51.4011, "lon": -0.2622,
                             "from": "naptan", "letter": None}  # fmt: skip
    assert r["stops"][0]["from"] == "tfl" and r["stops"][0]["letter"] == "K"
    assert r["shape_wkt"] == "MULTILINESTRING((-0.3 51.41,-0.26 51.4,-0.19 51.36))"


TIMETABLE = {"stops": [{"id": "490A", "name": "Kingston"}, {"id": "490B", "name": "New Malden"}],
             "timetable": {"departureStopId": "490A", "routes": [{
                 "stationIntervals": [{"id": "0", "intervals": [{"stopId": "490B", "timeToArrival": 7.0}]}],
                 "schedules": [{"name": "Saturday", "knownJourneys": [{"hour": "8", "minute": "0", "intervalId": 0}]},
                               {"name": "Monday to Friday", "knownJourneys": [{"hour": "7", "minute": "5", "intervalId": 0},
                                                                              {"hour": "7", "minute": "17", "intervalId": 0}]}]}]}}  # fmt: skip


def test_the_timetable_is_the_days_schedule_at_every_stop() -> None:
    name, rows = parse.timetable(TIMETABLE, date(2026, 11, 16))
    assert name == "Monday to Friday" and len(rows) == 4
    assert rows[1] == {"journey": 0, "stop": "490B", "stop_name": "New Malden", "stop_seq": 1,
                       "scheduled_at": datetime(2026, 11, 16, 7, 12, tzinfo=UTC)}  # fmt: skip
    assert parse.timetable(TIMETABLE, date(2026, 11, 14))[0] == "Saturday"
    assert parse.timetable(TIMETABLE, date(2026, 11, 15)) == (None, [])  # no Sunday schedule given


def test_status_takes_the_worst_and_keeps_every_disruption() -> None:
    s = parse.status([{"lineStatuses": [{"statusSeverity": 10, "statusSeverityDescription": "Good Service"}]}])
    assert s == {"severity": "Good Service", "reason": None, "disruptions": []}
    s = parse.status([{"lineStatuses": [{"statusSeverity": 6, "statusSeverityDescription": "Severe Delays", "reason": "Road works",
                                         "disruption": {"description": "Diverted"}}]}])  # fmt: skip
    assert s["severity"] == "Severe Delays" and s["disruptions"] == [{"description": "Diverted"}]


# --- a recorded minute, end to end (database) -------------------------------------------------------------------


class FakeRedis:
    def __init__(self) -> None:
        self.d: dict[str, Any] = {}

    async def get(self, k: str) -> Any:
        return self.d.get(k)

    async def set(self, k: str, v: Any, ex: int | None = None) -> None:
        self.d[k] = v.encode() if isinstance(v, str) else v

    def register_script(self, _lua: str) -> Any:  # the limiter is replaced in the test
        return None


@pytest.fixture
def db() -> Iterator[str]:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = importlib.import_module("migrations.versions.0048_buslog")
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        for t in mig.TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS app_setting"))
        conn.execute(text("CREATE TABLE app_setting (key text primary key, value jsonb not null, updated_at timestamptz not null default now())"))
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
    yield URL  # type: ignore[misc]
    with sync.begin() as conn:
        for t in mig.TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS app_setting"))
    sync.dispose()


def _upstream(fail_arrivals: bool) -> httpx.MockTransport:
    def handle(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if "bus-data" in req.url.host:
            assert req.url.params["lineRef"] == "213" and req.url.params["api_key"] == "bk"
            return httpx.Response(200, content=SIRI, headers={"content-type": "application/xml"})
        if p.endswith("/Arrivals"):
            if fail_arrivals:
                return httpx.Response(503, text="<html>busy</html>", headers={"content-type": "text/html"})
            return httpx.Response(200, json=[_arr("LJ1", "490B", "2026-11-16T07:15:00Z")])
        if "/Route/Sequence/" in p:
            return httpx.Response(200, json=ROUTE)
        if "/Timetable/" in p:
            return httpx.Response(200, json=TIMETABLE)
        if p.endswith("/Status"):
            return httpx.Response(200, json=[{"lineStatuses": [{"statusSeverity": 10, "statusSeverityDescription": "Good Service"}]}])
        return httpx.Response(404)

    return httpx.MockTransport(handle)


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
def test_a_minute_records_every_request_and_a_failure_is_not_a_missing_bus(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.modules.buslog import collect

    monkeypatch.setattr(collect, "BODS_EVERY", 30)
    monkeypatch.setattr(collect, "TFL_EVERY", 30)
    monkeypatch.setattr(collect, "SPEND_S", 3)

    class Now:
        async def acquire(self, max_wait: float = 0) -> None:
            return None

    async def run(fail: bool, redis: FakeRedis, prepared: bool) -> dict[str, Any]:
        engine = create_async_engine(db, poolclass=NullPool)
        async with httpx.AsyncClient(transport=_upstream(fail)) as client:
            rec = collect.Recorder(engine, redis, client, "213", date(2026, 11, 16))
            rec.tfl_key, rec.bods_key, rec.limiter = "tk", "bk", Now()  # type: ignore[assignment]
            out = await rec.minute(time.monotonic(), prepared)
        await engine.dispose()
        return out

    redis = FakeRedis()
    first = asyncio.run(run(False, redis, prepared=False))
    assert first["prepared"]["outbound"] == 3 and first["prepared"]["outbound_schedule"] == "Monday to Friday"
    assert first["requests"]["bods"] == {"ok": 1, "failed": 0, "empty": 0}
    second = asyncio.run(run(True, redis, prepared=True))
    assert second["requests"]["tfl-arrivals"] == {"ok": 0, "failed": 1, "empty": 0}

    sync = sa.create_engine(db)
    with sync.connect() as c:
        polls = c.execute(text("SELECT source, ok, status, error, url FROM buslog_poll ORDER BY id")).all()
        assert all("bk" not in p.url and "app_key" not in p.url for p in polls)  # keys never stored
        failed = [p for p in polls if not p.ok]
        assert [(p.source, p.status, p.error) for p in failed] == [("tfl-arrivals", 503, "HTTP 503")]
        # the failed request's answer is kept as it came
        body = c.execute(text("SELECT r.body FROM buslog_raw r JOIN buslog_poll p ON p.id = r.poll_id WHERE NOT p.ok")).scalar()
        assert gzip.decompress(body) == b"<html>busy</html>"
        # one "new" prediction, and no "gone" from the failed request
        assert c.execute(text("SELECT change FROM buslog_prediction")).scalars().all() == ["new"]
        pos = c.execute(text("SELECT vehicle, round(off_route_m) IS NOT NULL, fields->>'BlockRef' FROM buslog_position "
                             "WHERE lat IS NOT NULL")).all()  # fmt: skip
        assert pos and pos[0][0] == "LJ16EZX" and pos[0][1]
        assert c.execute(text("SELECT count(*) FROM buslog_timetable")).scalar() == 8  # 2 journeys x 2 stops x 2 ways
        assert c.execute(text("SELECT count(*) FROM buslog_route WHERE shape IS NOT NULL")).scalar() == 2
        assert json.loads(redis.d["wa:buslog:pred:213:2026-11-16"]).keys() == {"LJ1|490B"}
    sync.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
def test_the_api_shows_the_switches_and_the_record(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.main import app

    monkeypatch.setenv("API_TOKEN", "s3cret")
    h = {"Authorization": "Bearer s3cret"}
    app.state.engine = create_async_engine(db, poolclass=NullPool)
    app.state.redis = FakeRedis()
    c = TestClient(app)
    assert c.get("/v1/buslog/status").status_code == 401
    assert c.put("/v1/buslog/settings", json={"every_day": True}, headers=h).json()["every_day"] is True
    s = c.get("/v1/buslog/status", headers=h).json()
    tomorrow = (datetime.now(LON).date() + __import__("datetime").timedelta(days=1)).isoformat()
    assert s["settings"]["every_day"] is True and s["next_days"][0]["day"] == tomorrow  # every day counts while testing
    assert c.put("/v1/buslog/settings", json={"start": "09:00", "end": "08:00"}, headers=h).status_code == 422
    assert c.post("/v1/buslog/record-now", json={"minutes": 30}, headers=h).json()["record_until"]
    assert c.get("/v1/buslog/status", headers=h).json()["recording"] is True
    c.delete("/v1/buslog/record-now", headers=h)
    cal = c.get("/v1/buslog/calendar", headers=h).json()
    assert cal["school"] == "Coombe Girls School"
    assert c.put("/v1/buslog/calendar", json={**cal, "inset": ["2026-11-20"]}, headers=h).json()["inset"] == ["2026-11-20"]
    assert c.get("/v1/buslog/raw/999", headers=h).status_code == 404
    for path in ("/v1/buslog/route", "/v1/buslog/positions", "/v1/buslog/polls", "/v1/buslog/predictions?stop=490B"):
        assert c.get(path, headers=h).status_code == 200, path
    page = c.get("/buslog", headers=h)
    assert page.status_code == 200 and "busicon" in page.text
