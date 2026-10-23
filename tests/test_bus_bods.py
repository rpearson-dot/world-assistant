"""A bus line followed with BODS: its timetable file, its stops placed from GPS, and the setup fallback."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Self

import pytest
from fastapi.testclient import TestClient

from app import bus
from app.integrations.bods.client import BodsError, Box
from app.integrations.bods.txc import TxcError, parse_txc, seconds
from app.integrations.bus import bods_source
from app.integrations.bus.bods_source import Stop, arrivals, place_on_route, stop_distances
from app.integrations.bus.config import TrackerConfig
from app.main import app

NS = 'xmlns="http://www.transxchange.org.uk/"'
TXC = f"""<?xml version="1.0" encoding="UTF-8"?>
<TransXChange {NS}>
 <StopPoints>
  <AnnotatedStopPointRef><StopPointRef>0100A</StopPointRef><CommonName>Market Place</CommonName></AnnotatedStopPointRef>
  <AnnotatedStopPointRef><StopPointRef>0100B</StopPointRef><CommonName>Church Lane</CommonName></AnnotatedStopPointRef>
  <AnnotatedStopPointRef><StopPointRef>0100C</StopPointRef><CommonName>School</CommonName></AnnotatedStopPointRef>
  <AnnotatedStopPointRef><StopPointRef>0100D</StopPointRef><CommonName>Station</CommonName></AnnotatedStopPointRef>
 </StopPoints>
 <JourneyPatternSections>
  <JourneyPatternSection id="out">
   <JourneyPatternTimingLink id="o1"><From><StopPointRef>0100A</StopPointRef></From><To><StopPointRef>0100B</StopPointRef></To><RunTime>PT4M</RunTime></JourneyPatternTimingLink>
   <JourneyPatternTimingLink id="o2"><From><StopPointRef>0100B</StopPointRef></From><To><StopPointRef>0100C</StopPointRef></To><RunTime>PT3M30S</RunTime></JourneyPatternTimingLink>
   <JourneyPatternTimingLink id="o3"><From><StopPointRef>0100C</StopPointRef></From><To><StopPointRef>0100D</StopPointRef></To><RunTime>PT5M</RunTime></JourneyPatternTimingLink>
  </JourneyPatternSection>
  <JourneyPatternSection id="in">
   <JourneyPatternTimingLink id="i1"><From><StopPointRef>0100D</StopPointRef></From><To><StopPointRef>0100C</StopPointRef></To><RunTime>PT5M</RunTime></JourneyPatternTimingLink>
   <JourneyPatternTimingLink id="i2"><From><StopPointRef>0100C</StopPointRef></From><To><StopPointRef>0100A</StopPointRef></To><RunTime>PT8M</RunTime></JourneyPatternTimingLink>
  </JourneyPatternSection>
 </JourneyPatternSections>
 <Services><Service>
  <Lines><Line id="L1"><LineName>X26</LineName></Line></Lines>
  <StandardService>
   <JourneyPattern id="jp-out"><Direction>outbound</Direction><JourneyPatternSectionRefs>out</JourneyPatternSectionRefs></JourneyPattern>
   <JourneyPattern id="jp-in"><Direction>inbound</Direction><JourneyPatternSectionRefs>in</JourneyPatternSectionRefs></JourneyPattern>
  </StandardService>
 </Service>
 <Service><Lines><Line id="L2"><LineName>99</LineName></Line></Lines></Service></Services>
 <VehicleJourneys>
  <VehicleJourney><JourneyPatternRef>jp-out</JourneyPatternRef><LineRef>L1</LineRef><DepartureTime>07:10:00</DepartureTime>
   <OperatingProfile><RegularDayType><DaysOfWeek><MondayToFriday/></DaysOfWeek></RegularDayType></OperatingProfile></VehicleJourney>
  <VehicleJourney><JourneyPatternRef>jp-out</JourneyPatternRef><LineRef>L1</LineRef><DepartureTime>07:40:00</DepartureTime>
   <OperatingProfile><RegularDayType><DaysOfWeek><MondayToFriday/></DaysOfWeek></RegularDayType></OperatingProfile></VehicleJourney>
  <VehicleJourney><JourneyPatternRef>jp-out</JourneyPatternRef><LineRef>L1</LineRef><DepartureTime>09:00:00</DepartureTime>
   <OperatingProfile><RegularDayType><DaysOfWeek><Saturday/></DaysOfWeek></RegularDayType></OperatingProfile></VehicleJourney>
  <VehicleJourney><JourneyPatternRef>jp-in</JourneyPatternRef><LineRef>L1</LineRef><DepartureTime>08:00:00</DepartureTime></VehicleJourney>
 </VehicleJourneys>
</TransXChange>""".encode()


def test_a_timetable_file_gives_stops_in_order_and_weekday_times() -> None:
    route = parse_txc(TXC, "x26")  # the line is found whatever its case
    assert route is not None
    seq = route.sequences()
    assert [s["id"] for s in seq["outbound"]] == ["0100A", "0100B", "0100C", "0100D"]
    assert [s["name"] for s in seq["inbound"]] == ["Station", "School", "Market Place"]
    # leaving the first stop at 07:10 and 07:40; Church Lane is 4 minutes on, School 7.5 (rounded to 8)
    assert route.times_at("0100A", "outbound") == ["07:10", "07:40"]
    assert route.times_at("0100C", "outbound") == ["07:18", "07:48"]  # the Saturday-only 09:00 is not here
    assert route.times_at("0100C", "inbound") == ["08:05"] and route.times_at("0100C", "sideways") == []


def test_other_lines_bad_files_and_durations() -> None:
    assert parse_txc(TXC, "213") is None  # a file for another line
    assert parse_txc(TXC, "99") is None  # the line is listed but has no journeys
    with pytest.raises(TxcError, match="not valid XML"):
        parse_txc(b"<nope", "X26")
    with pytest.raises(TxcError, match="DOCTYPE"):
        parse_txc(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><TransXChange/>', "X26")
    assert [seconds(x) for x in ("PT2M", "PT1H5M30S", "PT45S", "", "nonsense")] == [120, 3930, 45, 0, 0]


# Four stops along a road east to west, 0.01 degrees of longitude (about 700 m) apart.
EAST = [Stop(f"S{i}", f"Stop {i}", 51.40, -0.30 + 0.01 * i) for i in range(4)]
WEST = [Stop(f"N{i}", f"Back {i}", 51.4001, -0.27 - 0.01 * i) for i in range(4)]
NOW = datetime(2026, 10, 23, 7, 30, tzinfo=UTC).timestamp()


def bus_at(lon: float, bearing: float | None, **extra: Any) -> dict[str, Any]:
    return {"vehicle": "BV01", "line": "X26", "lat": 51.40, "lon": lon, "bearing": bearing,
            "destination": None, "recorded_at": datetime.fromtimestamp(NOW - 20, UTC).isoformat(), **extra}  # fmt: skip


def test_a_bus_is_placed_along_its_route_and_only_stops_ahead_are_predicted() -> None:
    d = stop_distances(EAST)
    assert d[0] == 0 and 650 < d[1] < 750 and d[3] == pytest.approx(3 * d[1], rel=0.02)
    placed = place_on_route(EAST, 51.40, -0.285, 90.0)  # between stop 1 and 2
    assert placed and placed[1] < 10 and d[1] < placed[0] < d[2]
    out = arrivals([bus_at(-0.285, 90.0)], {"outbound": EAST, "inbound": WEST}, "x26", NOW)
    assert [p["naptanId"] for p in out] == ["S2", "S3"]  # stops 0 and 1 are behind it
    first = out[0]
    assert first["vehicleId"] == "BV01" and first["direction"] == "outbound"
    assert 60 < first["timeToStation"] < 100  # about 350 m at 4.5 m/s
    assert first["expectedArrival"].endswith("Z") and out[1]["timeToStation"] > first["timeToStation"]


def test_the_way_it_points_or_says_it_is_going_picks_the_direction() -> None:
    routes = {"outbound": EAST, "inbound": [Stop(s.sid, s.name, s.lat, s.lon) for s in reversed(EAST)]}
    west_bound = arrivals([bus_at(-0.285, 270.0)], routes, "X26", NOW)
    assert west_bound and {p["direction"] for p in west_bound} == {"inbound"}
    assert west_bound[0]["naptanId"] == "S1"  # heading back towards the first stop
    told = arrivals([bus_at(-0.285, None, destination="Stop 3")], routes, "X26", NOW)
    assert {p["direction"] for p in told} == {"outbound"}  # its destination board says so


def test_buses_that_cannot_be_used_are_left_out() -> None:
    routes = {"outbound": EAST, "inbound": WEST}
    stale = bus_at(-0.285, 90.0, recorded_at=datetime.fromtimestamp(NOW - 400, UTC).isoformat())
    far = {**bus_at(-0.285, 90.0), "lat": 51.45}  # 5 km off the road: a depot, not the route
    other = bus_at(-0.285, 90.0, line="99")
    assert arrivals([stale, far, other, bus_at(-0.285, 90.0, vehicle=None)], routes, "X26", NOW) == []
    assert arrivals([bus_at(-0.2999, 90.0)], routes, "X26", NOW)  # at the first stop: all the others are ahead
    assert arrivals([bus_at(-0.2699, 90.0)], routes, "X26", NOW) == []  # at the last stop: nothing ahead


def test_a_config_names_its_source_and_defaults_to_tfl() -> None:
    base: dict[str, Any] = {"line": "X26", "stops": [{"key": "a", "sid": "S0000001", "label": "A"}]}
    assert TrackerConfig.model_validate(base).source == "tfl"
    assert TrackerConfig.model_validate({**base, "source": "bods", "operator": "ABCD"}).operator == "ABCD"
    with pytest.raises(ValueError):
        TrackerConfig.model_validate({**base, "source": "other"})


class _NoTfl:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def route_sequence(self, line: str, direction: str) -> list[dict[str, str]]:
        return []


class _FakeBods:
    noc = "ABCD"

    def __init__(self, key: str, operator: str | None = None, box: object = None, check: object = None) -> None:
        self.line_asked = ""

    async def route_sequence(self, line: str, direction: str) -> list[dict[str, str]]:
        if line != "X26":
            raise BodsError(f"BODS has no timetable for line {line!r}")
        return [{"id": s["id"], "name": s["name"]} for s in (parse_txc(TXC, "X26") or None).sequences()[direction]]  # type: ignore[union-attr]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("API_TOKEN", "tok")
    monkeypatch.setenv("BODS_API_KEY", "key")
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(bus, "_tfl", lambda _r: _NoTfl())
    monkeypatch.setattr(bus, "BodsLineClient", _FakeBods)
    async def nowhere(*a: object, **k: object) -> None:
        return None

    monkeypatch.setattr(bus, "_home_box", nowhere)
    yield TestClient(app)  # type: ignore[misc]
    get_settings.cache_clear()


def test_a_line_tfl_does_not_know_is_found_on_bods(client: TestClient) -> None:
    h = {"Authorization": "Bearer tok"}
    d = client.get("/v1/bus/lines/X26/stops", headers=h).json()
    assert d["_source"] == "bods" and d["_operator"] == "ABCD"
    assert d["outbound"]["towards"] == "Station" and len(d["inbound"]["stops"]) == 3
    assert client.get("/v1/bus/lines/77/stops", headers=h).status_code == 404  # no timetable anywhere


def test_without_a_bods_key_the_reason_says_what_to_do(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("BODS_API_KEY", "")
    get_settings.cache_clear()
    r = client.get("/v1/bus/lines/X26/stops", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 404 and "BODS key" in r.json()["detail"]


def test_the_bods_client_reads_each_file_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def load(key: str, line: str, noc: str | None = None, box: object = None, check: object = None) -> Any:
        calls.append(line)
        return parse_txc(TXC, line), "ABCD"

    monkeypatch.setattr(bods_source, "load_route", load)
    import asyncio

    async def go() -> tuple[list[dict[str, str]], list[str], str | None]:
        c = bods_source.BodsLineClient("key")
        a = await c.route_sequence("X26", "outbound")
        await c.route_sequence("X26", "inbound")
        return a, await c.timetable_times("X26", "0100B", "outbound"), c.noc

    seq, times, noc = asyncio.run(go())
    assert len(seq) == 4 and times == ["07:14", "07:44"] and noc == "ABCD" and calls == ["X26"]


class _Conn:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None


class _Engine:
    def connect(self) -> _Conn:
        return _Conn()


@pytest.mark.asyncio
async def test_a_poll_of_a_bods_line_turns_positions_into_predictions(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations.bus import poller, store

    seen: dict[str, Any] = {}

    async def coords(conn: object, sids: list[str]) -> dict[str, tuple[float, float]]:
        return {s.sid: (s.lat, s.lon) for s in EAST if s.sid in sids}  # the register has all but "GONE"

    async def vehicles(key: str, box: Any) -> list[dict[str, Any]]:
        seen["box"] = box
        return [bus_at(-0.285, 90.0)]

    monkeypatch.setattr(store, "stop_coords", coords)
    monkeypatch.setattr(poller, "fetch_vehicles", vehicles)
    cfg = TrackerConfig.model_validate({"line": "X26", "source": "bods", "stops": [{"key": "a", "sid": "S0000001", "label": "A"}]})
    rows = [{"direction": "outbound", "pos": i, "sid": s.sid, "name": s.name} for i, s in enumerate(EAST)]
    rows.append({"direction": "outbound", "pos": 4, "sid": "GONE", "name": "Not in the register"})
    preds = await poller._bods_arrivals(_Engine(), cfg, rows, NOW)  # type: ignore[arg-type]
    assert [p["naptanId"] for p in preds] == ["S2", "S3"]
    box = seen["box"]
    assert box.min_lon < -0.30 and box.max_lon > -0.27 and box.min_lat < 51.40 < box.max_lat  # round the whole route
    with pytest.raises(BodsError, match="stop register"):
        await poller._bods_arrivals(_Engine(), cfg, [{"direction": "outbound", "pos": 0, "sid": "GONE", "name": ""}], NOW)  # type: ignore[arg-type]


# --- the wrong-route guard -----------------------------------------------------------------------------

LONDON = Box(-0.4, 51.2, -0.1, 51.5)  # where the buses are
STOPS_AT = {"0100A": (51.37, -0.25), "0100B": (51.38, -0.24), "0100C": (51.39, -0.23), "0100D": (51.40, -0.22)}


def _patch_bods(monkeypatch: pytest.MonkeyPatch, nocs: list[str], datasets: dict[str, list[str]]) -> list[tuple[str, str]]:
    asked: list[tuple[str, str]] = []

    async def operators(key: str, line: str, box: Box) -> list[str]:
        return nocs

    async def find(key: str, noc: str | None = None, search: str | None = None, **k: object) -> list[dict[str, str]]:
        asked.append(("noc" if noc else "search", noc or search or ""))
        return [{"url": u, "name": u} for u in datasets.get(noc or "", [])]

    async def download(url: str) -> list[bytes]:
        return [TXC]

    monkeypatch.setattr(bods_source, "operators_of_line", operators)
    monkeypatch.setattr(bods_source, "find_datasets", find)
    monkeypatch.setattr(bods_source, "download_txc", download)
    return asked


async def test_a_line_number_alone_is_never_searched_for(monkeypatch: pytest.MonkeyPatch) -> None:
    """TfL's bus companies publish no timetable files; the old code then searched the number across Britain."""
    asked = _patch_bods(monkeypatch, ["TFLO"], {})
    with pytest.raises(BodsError, match="no timetable files for TFLO"):
        await bods_source.load_route("k", "X26", None, LONDON)
    assert asked == [("noc", "TFLO")]  # and no search call


async def test_without_an_operator_or_a_place_it_says_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_bods(monkeypatch, [], {})
    with pytest.raises(BodsError, match="give its operator or a place"):
        await bods_source.load_route("k", "X26")


async def test_a_timetable_for_a_route_elsewhere_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_bods(monkeypatch, ["ABCD"], {"ABCD": ["u"]})

    def coords_at(where: dict[str, tuple[float, float]]):  # type: ignore[no-untyped-def]
        async def coords(sids: list[str]) -> dict[str, tuple[float, float]]:
            return {s: where[s] for s in sids if s in where}
        return coords

    here = bods_source.near_check(coords_at(STOPS_AT), LONDON)
    route, noc = await bods_source.load_route("k", "X26", None, LONDON, here)
    assert noc == "ABCD" and route.line == "X26"

    manchester = {s: (53.48, -2.24) for s in STOPS_AT}  # the same line number, a different town
    with pytest.raises(BodsError, match="no timetable for line"):
        await bods_source.load_route("k", "X26", None, LONDON, bods_source.near_check(coords_at(manchester), LONDON))


async def test_with_no_stop_placed_the_check_has_nothing_to_judge_by(monkeypatch: pytest.MonkeyPatch) -> None:
    async def none(sids: list[str]) -> dict[str, tuple[float, float]]:
        return {}

    route = parse_txc(TXC, "X26")
    assert route and await bods_source.near_check(none, LONDON)(route) is True
