"""Routes added from Home Assistant: naming, the cap, building, and removal."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app import ha_routes, routing
from app.config import get_settings
from tests.test_routing import body, encode

URL = os.environ.get("WA_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")


def _route(**kw: Any) -> ha_routes.RouteIn:
    base: dict[str, Any] = {"from_name": "Home", "from_lat": 51.37, "from_lon": -0.25,
                            "to_name": "Work", "to_lat": 51.5, "to_lon": -0.12}  # fmt: skip
    return ha_routes.RouteIn(**{**base, **kw})


def test_ids_are_prefixed_slugged_and_short() -> None:
    assert ha_routes.place_id("Home") == "ha-home"
    assert ha_routes.place_id("Mum's House!") == "ha-mum-s-house"
    assert ha_routes.place_id("A very long place name indeed") == "ha-a-very-long-plac"
    assert ha_routes.place_id("???") == "ha-x"
    tid = ha_routes.trip_id(ha_routes.place_id("Home"), ha_routes.place_id("Work"))
    assert tid == "ha-home-to-work"
    longest = ha_routes.trip_id(ha_routes.place_id("a" * 40), ha_routes.place_id("b" * 40))
    from app.places import check_slug

    assert len(longest) == 39 and check_slug(longest) == longest  # a valid id for the web pages too


def test_a_route_needs_two_different_british_ends() -> None:
    assert _route().both_ways is True
    with pytest.raises(ValueError, match="different names"):
        _route(to_name="home")  # same place once slugged
    with pytest.raises(ValueError):
        _route(to_lat=10.0)
    with pytest.raises(ValueError):
        _route(from_name="")


# --- storage (needs Postgres) ------------------------------------------------------------------

VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
NAMES = ["0003_nh_storage", "0006_places_trips", "0008_trip_routes", "0017_ha_origin"]
TABLES = ["trip", "place", "nh_sign_message", "nh_sign", "nh_event_corridor", "nh_event", "nh_corridor"]


def _migrate(direction: str) -> None:
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        if direction == "upgrade":
            for t in TABLES:
                conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        with Operations.context(MigrationContext.configure(conn)):
            for n in NAMES if direction == "upgrade" else reversed(NAMES):
                spec = importlib.util.spec_from_file_location(n, VERSIONS / f"{n}.py")
                assert spec and spec.loader
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                getattr(m, direction)()
    sync.dispose()


@pytest.fixture
def schema() -> Iterator[None]:
    _migrate("upgrade")
    yield
    _migrate("downgrade")


@pytest_asyncio.fixture
async def engine(schema: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(URL)  # type: ignore[arg-type]
    monkeypatch.setenv("WA_HA_ROUTES_MAX", "4")
    get_settings.cache_clear()
    yield eng
    get_settings.cache_clear()
    await eng.dispose()


def _valhalla(monkeypatch: pytest.MonkeyPatch, *, up: bool = True) -> None:
    pts = [(51.37, -0.25), (51.4, -0.2), (51.5, -0.12)]

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body(encode(pts))) if up else httpx.Response(503)

    class Client(routing.ValhallaClient):
        def __init__(self, base_url: str, **kw: Any) -> None:
            super().__init__("http://v", transport=httpx.MockTransport(handler))

    monkeypatch.setattr(routing, "ValhallaClient", Client)


def _request(engine: AsyncEngine) -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=engine)))


@needs_db
async def test_adding_a_route_saves_both_ways_builds_them_and_removing_cleans_up(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _valhalla(monkeypatch)
    req = _request(engine)
    out = await ha_routes.add_route(_route(), req)
    assert out["ok"] is True and out["built"] is True and "M23" in out["message"]
    listing = await ha_routes.list_routes(req)
    assert [r["id"] for r in listing["routes"]] == ["ha-home-to-work", "ha-work-to-home"]
    assert all(r["distance_km"] == 100.4 for r in listing["routes"]) and listing["max"] == 4
    async with engine.connect() as conn:
        places = (await conn.execute(text("SELECT id, origin FROM place ORDER BY id"))).all()
        corridors = [r[0] for r in await conn.execute(text("SELECT id FROM nh_corridor ORDER BY id"))]
    assert places == [("ha-home", "home-assistant"), ("ha-work", "home-assistant")]
    assert corridors == ["trip-ha-home-to-work", "trip-ha-work-to-home"]

    again = await ha_routes.add_route(_route(to_lat=51.6), req)  # the same ends again: updated, not doubled
    assert again["ok"] is True and len((await ha_routes.list_routes(req))["routes"]) == 2

    gone = await ha_routes.delete_route(ha_routes.EndsIn(from_name="Home", to_name="Work"), req)
    assert gone["ok"] is True
    assert (await ha_routes.list_routes(req))["routes"] == []
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM place"))).scalar_one() == 0  # nothing uses them
        assert (await conn.execute(text("SELECT count(*) FROM nh_corridor"))).scalar_one() == 0  # the roads sensor goes too
    assert (await ha_routes.delete_route(ha_routes.EndsIn(from_name="Home", to_name="Work"), req))["ok"] is False


@needs_db
async def test_the_cap_counts_each_direction_and_refuses_politely(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    _valhalla(monkeypatch)
    req = _request(engine)
    assert (await ha_routes.add_route(_route(), req))["ok"] is True  # 2 of 4
    assert (await ha_routes.add_route(_route(to_name="School", to_lat=51.4), req))["ok"] is True  # 4 of 4
    refused = await ha_routes.add_route(_route(to_name="Gym", to_lat=51.45), req)
    assert refused["ok"] is False and "limit of 4" in refused["message"]
    one_way = await ha_routes.delete_route(ha_routes.EndsIn(from_name="Home", to_name="School", both_ways=False), req)
    assert one_way["ok"] is True  # frees one
    assert (await ha_routes.add_route(_route(to_name="Gym", to_lat=51.45, both_ways=False), req))["ok"] is True


@needs_db
async def test_places_made_on_the_web_pages_are_never_touched(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    _valhalla(monkeypatch)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO place (id, name, kind, lat, lon) VALUES ('home','Home','home',51.3,-0.5)"))
    req = _request(engine)
    await ha_routes.add_route(_route(), req)
    await ha_routes.delete_route(ha_routes.EndsIn(from_name="Home", to_name="Work"), req)
    async with engine.connect() as conn:
        assert [r[0] for r in await conn.execute(text("SELECT id FROM place"))] == ["home"]


@needs_db
async def test_a_route_that_cannot_be_built_yet_is_still_saved(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    _valhalla(monkeypatch, up=False)
    req = _request(engine)
    out = await ha_routes.add_route(_route(), req)
    assert out["ok"] is True and out["built"] is False and "could not be built yet" in out["message"]
    assert len((await ha_routes.list_routes(req))["routes"]) == 2
