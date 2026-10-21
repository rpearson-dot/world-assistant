"""Extra places Home Assistant asks for events at: the cap, the time-out, and what gets published."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import ha, ha_locations, ha_publish
from app.config import get_settings


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("WA_HA_LOCATIONS_MAX", "8")
    monkeypatch.setenv("WA_HA_LOCATION_TTL_DAYS", "30")
    monkeypatch.setenv("WA_HA_LOCATION_PRESSURE_TTL_DAYS", "7")
    monkeypatch.setenv("WA_HA_LOCATION_REFRESH_H", "12")
    monkeypatch.setenv("SKIDDLE_API_KEY", "k")
    monkeypatch.setenv("TICKETMASTER_API_KEY", "k")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_the_timeout_shortens_when_the_cap_is_three_quarters_used() -> None:
    assert ha_locations.limits(0)["ttl_days"] == 30
    assert ha_locations.limits(5)["ttl_days"] == 30
    assert ha_locations.limits(6)["ttl_days"] == 7  # 75% of 8
    assert ha_locations.limits(8)["ttl_days"] == 7


def test_the_cost_in_provider_calls_is_stated() -> None:
    lim = ha_locations.limits(4)
    assert lim["max"] == 8 and lim["provider_calls_per_day"] == 4 * 2 * 2  # places x searches a day x sources


def test_events_device_has_a_sensor_per_place_plus_a_summary() -> None:
    data = {
        "limits": {"count": 2, "max": 8, "pressure_at": 6, "ttl_days": 30, "refresh_hours": 3, "provider_calls_per_day": 32},
        "locations": [
            {"id": "mum-s-house", "name": "Mum's house", "lat": 52.0, "lon": -1.0, "radius_km": 8, "days": 7, "count": 1,
             "expires_at": "2026-11-20T00:00:00Z", "errors": [],
             "events": [{"name": "Gig", "date": "2026-10-25", "venue": "V", "distance_km": 2.0, "source": "skiddle"}]},
            {"id": "work", "name": "Work", "lat": 51.0, "lon": -0.5, "radius_km": 8, "days": 7, "count": 0, "events": [], "errors": []},
        ],
    }  # fmt: skip
    d = ha_publish.events_device(data)
    assert d.id == "events"
    keys = [s.key for s in d.sensors]
    assert keys == ["locations", "place_mum_s_house", "place_work"]
    place = d.sensors[1]
    assert place.state == 1 and place.attributes["latitude"] == 52.0 and place.attributes["events"][0]["name"] == "Gig"
    assert d.sensors[0].state == 2 and d.sensors[0].attributes["max"] == 8
    assert ha.unique_id("events", place.key) == "wa_events_place_mum_s_house"


def test_an_expired_place_has_its_entity_and_retained_state_removed() -> None:
    out = ha.clear("wa", "homeassistant", "events", ["place_old"])
    topics = [t for t, _, _ in out]
    assert "homeassistant/sensor/wa_events_place_old/config" in topics
    assert "wa/events/place_old" in topics and "wa/events/place_old/attributes" in topics
    assert all(payload == "" and retain for _, payload, retain in out)


@pytest.mark.parametrize("bad", [{"name": "", "lat": 52, "lon": -1}, {"name": "x", "lat": 10, "lon": -1}, {"name": "x", "lat": 52, "lon": 40}])
def test_a_place_needs_a_name_and_a_british_position(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        ha_locations.LocationIn(**bad)


# --- storage (needs Postgres) ------------------------------------------------------------------

URL = os.environ.get("WA_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
MIGRATION = Path(__file__).parent.parent / "migrations" / "versions" / "0016_ha_location.py"


@pytest.fixture
def table() -> Iterator[None]:
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS ha_location"))
        with Operations.context(MigrationContext.configure(conn)):
            spec = importlib.util.spec_from_file_location("m0016", MIGRATION)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()
    yield
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS ha_location"))
    sync.dispose()


@needs_db
async def test_add_renew_cap_and_expire(table: None) -> None:
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=engine)))
    try:
        for i in range(8):
            await ha_locations.add_location(ha_locations.LocationIn(name=f"Place {i}", lat=52.0, lon=-1.0), request)
        refused = await ha_locations.add_location(ha_locations.LocationIn(name="One too many", lat=52.0, lon=-1.0), request)
        assert refused["ok"] is False and "maximum of 8" in refused["message"]
        again = await ha_locations.add_location(ha_locations.LocationIn(name="Place 3", lat=52.5, lon=-1.0), request)
        assert again["entity"] == "sensor.wa_events_place_place_3"  # renewing is allowed at the cap
        listing = await ha_locations.list_locations(request)
        assert len(listing["locations"]) == 8 and listing["limits"]["ttl_days"] == 7  # under pressure
        async with engine.begin() as conn:  # nine days without renewal: gone under the shorter time-out
            await conn.execute(text("UPDATE ha_location SET last_seen = now() - interval '9 days' WHERE id <> 'place_3'"))
        listing = await ha_locations.list_locations(request)
        assert [r["id"] for r in listing["locations"]] == ["place_3"]
        assert listing["limits"]["ttl_days"] == 30  # pressure over
        assert (await ha_locations.delete_by_name(ha_locations.NameIn(name="Place 3"), request))["ok"] is True
        assert (await ha_locations.delete_by_name(ha_locations.NameIn(name="Place 3"), request))["ok"] is False
    finally:
        await engine.dispose()
