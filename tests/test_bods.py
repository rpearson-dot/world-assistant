"""Live buses from BODS: the SIRI-VM parser, the box, the key handling and the cache."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import bods
from app.integrations.bods import client
from app.integrations.bods.client import BodsError, Box, box_around, fetch_vehicles, parse_vehicles
from app.main import app

SIRI = b"""<?xml version="1.0" encoding="UTF-8"?>
<Siri xmlns="http://www.siri.org.uk/siri" version="2.0">
 <ServiceDelivery><VehicleMonitoringDelivery><VehicleActivity>
  <RecordedAtTime>2026-10-22T09:30:00+00:00</RecordedAtTime>
  <MonitoredVehicleJourney>
   <LineRef>ULBF:213:1</LineRef><PublishedLineName>213</PublishedLineName>
   <DirectionRef>outbound</DirectionRef><OperatorRef>LUAS</OperatorRef>
   <DestinationName>Kingston</DestinationName>
   <VehicleLocation><Longitude>-0.1280</Longitude><Latitude>51.5080</Latitude></VehicleLocation>
   <Bearing>90.5</Bearing><VehicleRef>LJ12ABC</VehicleRef>
  </MonitoredVehicleJourney>
 </VehicleActivity>
 <VehicleActivity>
  <RecordedAtTime>2026-10-22T09:30:00</RecordedAtTime>
  <MonitoredVehicleJourney><LineRef>X1</LineRef><OperatorRef>ABCD</OperatorRef>
   <VehicleLocation><Longitude>-0.1400</Longitude><Latitude>51.5200</Latitude></VehicleLocation>
   <VehicleRef>BUS2</VehicleRef></MonitoredVehicleJourney>
 </VehicleActivity>
 <VehicleActivity>
  <MonitoredVehicleJourney><LineRef>NOPOS</LineRef><VehicleRef>BUS3</VehicleRef></MonitoredVehicleJourney>
 </VehicleActivity>
 <VehicleActivity>
  <MonitoredVehicleJourney><LineRef>LOST</LineRef>
   <VehicleLocation><Longitude>0</Longitude><Latitude>0</Latitude></VehicleLocation></MonitoredVehicleJourney>
 </VehicleActivity>
 </VehicleMonitoringDelivery></ServiceDelivery></Siri>"""

HOME = (51.5074, -0.1278)


def test_the_parser_reads_buses_ignoring_namespaces_and_dropping_ones_without_a_position() -> None:
    buses = parse_vehicles(SIRI)
    assert [b["vehicle"] for b in buses] == ["LJ12ABC", "BUS2"]  # no position, and Null Island, are out
    first = buses[0]
    assert first["line"] == "213" and first["operator"] == "LUAS" and first["destination"] == "Kingston"
    assert first["bearing"] == 90.5 and (first["lat"], first["lon"]) == (51.508, -0.128)
    assert first["recorded_at"] == "2026-10-22T09:30:00+00:00"
    second = buses[1]
    assert second["line"] == "X1" and second["bearing"] is None  # falls back to LineRef; no bearing given
    assert second["recorded_at"].endswith("+00:00")  # a time with no offset is read as UTC


def test_hostile_or_broken_xml_is_refused() -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaa">]><Siri>&a;</Siri>'
    with pytest.raises(BodsError, match="DOCTYPE"):
        parse_vehicles(bomb)
    with pytest.raises(BodsError, match="not valid XML"):
        parse_vehicles(b"<Siri><oops>")
    with pytest.raises(BodsError, match="larger"):
        parse_vehicles(b"x" * (client.MAX_BYTES + 1))
    assert parse_vehicles(b"<Siri/>") == []  # an empty feed is a real answer


def test_the_box_is_longitude_first_and_contains_its_circle() -> None:
    box = box_around(*HOME, 5.0)
    assert box.min_lon < HOME[1] < box.max_lon and box.min_lat < HOME[0] < box.max_lat
    assert (box.max_lat - box.min_lat) / 2 == pytest.approx(5 / 111, rel=0.01)
    assert (box.max_lon - box.min_lon) > (box.max_lat - box.min_lat)  # a degree of longitude is shorter here
    assert Box(-1.0, 52.0, 0.5, 53.0).param() == "-1.0000,52.0000,0.5000,53.0000"


async def test_the_key_goes_in_the_request_and_a_refusal_is_explained() -> None:
    seen: dict[str, Any] = {}

    def ok(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, content=SIRI)

    c = httpx.AsyncClient(transport=httpx.MockTransport(ok))
    assert len(await fetch_vehicles("KEY", box_around(*HOME, 1.0), c)) == 2
    assert seen["api_key"] == "KEY" and seen["boundingBox"].count(",") == 3

    for status, words in ((401, "refused the key"), (500, "HTTP 500")):
        bad = httpx.AsyncClient(transport=httpx.MockTransport(lambda r, s=status: httpx.Response(s)))
        with pytest.raises(BodsError, match=words):
            await fetch_vehicles("KEY", box_around(*HOME, 1.0), bad)
    with pytest.raises(BodsError, match="not set"):
        await fetch_vehicles("", box_around(*HOME, 1.0))


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.sets = 0

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value
        self.sets += 1


async def test_a_second_look_within_thirty_seconds_does_not_call_bods(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODS_API_KEY", "KEY")
    calls = []

    async def fake_fetch(key: str, box: Box, c: Any = None) -> list[dict[str, Any]]:
        calls.append(key)
        return parse_vehicles(SIRI)

    monkeypatch.setattr(bods, "fetch_vehicles", fake_fetch)
    redis = FakeRedis()
    first = await bods.vehicles_near(redis, *HOME, 5.0)
    again = await bods.vehicles_near(redis, *HOME, 5.0)
    assert len(calls) == 1 and [b["vehicle"] for b in again] == [b["vehicle"] for b in first]
    assert first[0]["km"] < first[1]["km"]  # nearest first
    only = await bods.vehicles_near(redis, *HOME, 5.0, line="x1")
    assert [b["vehicle"] for b in only] == ["BUS2"]  # the line filter ignores case
    tight = await bods.vehicles_near(redis, *HOME, 0.05)
    assert tight == []  # both are further than 50 m: the box is cut down to the circle
    assert [b["vehicle"] for b in await bods.vehicles_near(redis, *HOME, 0.2)] == ["LJ12ABC"]  # 90 m away


async def test_a_broken_cache_does_not_break_the_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    class Down:
        async def get(self, key: str) -> None:
            raise ConnectionError

        async def set(self, *a: Any, **k: Any) -> None:
            raise ConnectionError

    async def fake_fetch(key: str, box: Box, c: Any = None) -> list[dict[str, Any]]:
        return parse_vehicles(SIRI)

    monkeypatch.setenv("BODS_API_KEY", "KEY")
    monkeypatch.setattr(bods, "fetch_vehicles", fake_fetch)
    assert len(await bods.vehicles_near(Down(), *HOME, 5.0)) == 2


def test_the_endpoint_asks_for_a_key_and_reports_bods_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    app.state.redis = FakeRedis()
    c, h = TestClient(app), {"Authorization": "Bearer tok"}
    assert c.get(f"/v1/live-buses?lat={HOME[0]}&lon={HOME[1]}").status_code == 401

    monkeypatch.delenv("BODS_API_KEY", raising=False)
    monkeypatch.setattr("app.config.SECRETS_DIR", Path("/nonexistent"))
    r = c.get(f"/v1/live-buses?lat={HOME[0]}&lon={HOME[1]}", headers=h)
    assert r.status_code == 503 and "bods_api_key" in r.json()["detail"]

    monkeypatch.setenv("BODS_API_KEY", "KEY")

    async def fake_fetch(key: str, box: Box, cl: Any = None) -> list[dict[str, Any]]:
        return parse_vehicles(SIRI)

    monkeypatch.setattr(bods, "fetch_vehicles", fake_fetch)
    ok = c.get(f"/v1/live-buses?lat={HOME[0]}&lon={HOME[1]}&radius_km=5", headers=h).json()
    assert ok["count"] == 2 and ok["buses"][0]["vehicle"] == "LJ12ABC"

    async def broken(key: str, box: Box, cl: Any = None) -> list[dict[str, Any]]:
        raise BodsError("the bus feed answered HTTP 500")

    app.state.redis = FakeRedis()
    monkeypatch.setattr(bods, "fetch_vehicles", broken)
    r = c.get(f"/v1/live-buses?lat={HOME[0]}&lon={HOME[1]}", headers=h)
    assert r.status_code == 502 and "HTTP 500" in r.json()["detail"]
    assert json.dumps(r.json())  # a plain error, not a traceback
