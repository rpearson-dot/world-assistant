from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app import ha, ha_publish, mqtt
from app.ha import Device, Sensor

POLLEN = {
    "place": "Home", "source": "pollen.example", "dates": ["2026-10-21", "2026-10-22"],
    "allergy_risk": [3.5, 4.0], "allergy_risk_level": [2, 2], "allergy_risk_label": ["Low", "Low"],
    "allergens": [{"id": "grass", "name": "Grass", "levels": [0, 1], "labels": ["None", "Low"]},
                  {"id": "birch tree", "name": "Birch", "levels": [None, 1], "labels": [None, "Low"]}],
}  # fmt: skip


def test_slug_is_topic_safe() -> None:
    assert ha.slug("Birch Tree/Pollen!") == "birch_tree_pollen"
    assert ha.slug("!!!") == "x"


def test_discovery_has_expiry_unique_ids_and_only_set_fields() -> None:
    dev = Device("pollen", "Pollen", [Sensor("a", "A", 1, unit="min"), Sensor("b", "B", "x")])
    msgs = ha.discovery("wa", "homeassistant", dev, 900)
    assert [m[0] for m in msgs] == [
        "homeassistant/sensor/wa_pollen_a/config", "homeassistant/sensor/wa_pollen_b/config"]
    a, b = (json.loads(m[1]) for m in msgs)
    assert all(m[2] for m in msgs) and a["expire_after"] == 900
    assert a["unit_of_measurement"] == "min" and "unit_of_measurement" not in b
    assert a["state_topic"] == "wa/pollen/a" and a["json_attributes_topic"] == "wa/pollen/a/attributes"
    assert a["device"]["identifiers"] == ["world_assistant_pollen"]


def test_states_are_retained_and_none_is_empty() -> None:
    dev = Device("d", "D", [Sensor("a", "A", None, attributes={"k": 1})])
    msgs = ha.states("wa", dev)
    assert msgs == [("wa/d/a", "", True), ("wa/d/a/attributes", '{"k": 1}', True)]


def test_removals_clear_the_config() -> None:
    assert ha.removals("homeassistant", "d", ["a"]) == [("homeassistant/sensor/wa_d_a/config", "", True)]


def test_pollen_device() -> None:
    d = ha_publish.pollen_device(POLLEN)
    by = {s.key: s for s in d.sensors}
    assert by["allergy_risk"].state == 3.5 and by["allergy_risk_label"].state == "Low"
    assert by["pollen_grass"].state == 0 and by["pollen_birch_tree"].state is None
    assert by["pollen_grass"].attributes["levels"] == [0, 1]


def test_fuel_device_handles_no_stations() -> None:
    best = {"pence": 131.9, "trading_name": "Shell X", "brand_name": "Shell", "distance_km": 1.2}
    d = ha_publish.fuel_device({"E10": {"cheapest_overall": best, "stations": [best]},
                                "B7_STANDARD": {"cheapest_overall": None, "stations": []}})  # fmt: skip
    e10, b7 = d.sensors
    assert e10.key == "cheapest_e10" and e10.state == 131.9 and e10.unit == "p"
    assert e10.attributes["station"] == "Shell X"
    assert b7.state is None and b7.attributes["nearest"] == []


def test_trains_device_counts_disruption() -> None:
    board = {"id": "home-am", "name": "Morning train"}
    answer = {"stale": False, "as_of": "t", "departures": [
        {"destination": "A", "minutes": 4, "status": "OnTime", "delay_minutes": 0},
        {"destination": "B", "minutes": 9, "status": "Delayed", "delay_minutes": 7},
        {"destination": "C", "minutes": 15, "status": "Cancelled", "delay_minutes": None}]}  # fmt: skip
    d = ha_publish.trains_device([(board, answer), ({"id": "empty", "name": "Empty"}, {})])
    by = {s.key: s for s in d.sensors}
    assert by["home_am_next"].state == 4 and by["home_am_next"].attributes["destination"] == "A"
    assert by["home_am_disrupted"].state == 2
    assert by["empty_next"].state is None and by["empty_disrupted"].state == 0


def test_roads_device_skips_disabled_corridors() -> None:
    status = {"corridors": [{"id": "m1", "name": "M1", "enabled": True, "live_events": 3, "active_events": 1},
                            {"id": "old", "name": "Old", "enabled": False}],
              "events_live": [{"n": 2}, {"n": 3}]}  # fmt: skip
    d = ha_publish.roads_device(status)
    assert [s.key for s in d.sensors] == ["corridor_m1", "live_events"]
    assert d.sensors[0].state == 1 and d.sensors[1].state == 5


def test_events_device() -> None:
    data = {"count": 2, "days": 7, "place": "Home",
            "events": [{"name": "Gig", "date": "2026-10-25", "venue": "Hall"}, {"name": "Fair"}]}  # fmt: skip
    up, nxt = ha_publish.events_device(data).sensors
    assert up.state == 2 and nxt.state == "Gig" and nxt.attributes["venue"] == "Hall"
    assert ha_publish.events_device({}).sensors[1].state is None


def _client(routes: dict[str, Any]) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        body = routes.get(req.url.path)
        if body is None:
            return httpx.Response(503, json={"detail": "not set up"})
        return httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://api")


@pytest.mark.asyncio
async def test_collect_skips_domains_that_fail() -> None:
    routes = {"/v1/pollen/near": POLLEN, "/v1/status": {"corridors": [], "events_live": []},
              "/v1/tfl/boards": [{"id": "b", "name": "B"}]}  # fmt: skip
    async with _client(routes) as c:
        devices = await ha_publish.collect(c)
    assert [d.id for d in devices] == ["pollen", "trains", "roads"]  # fuel and events 503; the board 503s too
    assert next(d for d in devices if d.id == "trains").sensors == []


@pytest.mark.asyncio
async def test_ha_sync_publishes_discovery_once_then_state_only(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[list[mqtt.Message]] = []

    async def fake_publish(msgs: list[mqtt.Message]) -> bool:
        sent.append(msgs)
        return True

    real = httpx.AsyncClient
    monkeypatch.setattr(mqtt, "publish", fake_publish)
    monkeypatch.setattr(ha_publish.httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=POLLEN) if r.url.path == "/v1/pollen/near"
                                      else httpx.Response(503)), base_url="http://api"))
    ctx: dict[str, Any] = {}
    assert await ha_publish.ha_sync(ctx) > 0
    assert await ha_publish.ha_sync(ctx) > 0
    first, second = sent
    assert any(t.endswith("/config") for t, _, _ in first)
    assert not any(t.endswith("/config") for t, _, _ in second)
    assert all(r for _, _, r in first + second)


@pytest.mark.asyncio
async def test_ha_sync_off_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_HA_PUBLISH", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        assert await ha_publish.ha_sync({}) == 0
    finally:
        get_settings.cache_clear()
