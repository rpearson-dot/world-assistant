from __future__ import annotations

import importlib.util
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.integrations.nationalhighways.store import load_corridors
from app.routing import (
    RoutingError,
    ValhallaClient,
    build_trips,
    decode_polyline,
    parse_route,
    road_numbers,
    simplify,
)

URL = os.environ.get("WA_TEST_DATABASE_URL")


def encode(points: list[tuple[float, float]], precision: int = 6) -> str:
    out, last = [], (0, 0)
    for lat, lon in points:
        cur = (round(lat * 10**precision), round(lon * 10**precision))
        for a, b in zip(cur, last, strict=True):
            v = a - b
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        last = cur
    return "".join(out)


def test_decode_known_google_example() -> None:
    pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5)
    assert pts == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


def test_decode_roundtrip() -> None:
    pts = [(51.2991, -0.4871), (51.3, -0.49), (51.2, 0.1), (50.8225, -0.1372)]
    assert decode_polyline(encode(pts)) == pts


def test_simplify_drops_straight_points_and_keeps_corners() -> None:
    line = [(51.0 + i * 0.001, -0.5) for i in range(50)]  # straight north
    assert simplify(line) == [line[0], line[-1]]
    bent = line[:25] + [(51.025 + i * 0.001, -0.5 + i * 0.001) for i in range(25)]
    out = simplify(bent)
    assert out[0] == bent[0] and out[-1] == bent[-1] and 3 <= len(out) < 10
    assert simplify([(1.0, 1.0)]) == [(1.0, 1.0)]


def test_road_numbers_keep_motorways_and_a_roads_only() -> None:
    man = [{"street_names": ["M25"]}, {"street_names": ["Church Lane"]}, {"street_names": ["A23"]},
           {"street_names": ["M25"]}, {}, {"street_names": ["A3(M)", "B2036"]}]  # fmt: skip
    assert road_numbers(man) == ("M25", "A23", "A3(M)")


def body(shape: str) -> dict:
    return {"trip": {"summary": {"length": 100.4, "time": 4500.2},
            "legs": [{"shape": shape, "maneuvers": [{"street_names": ["M23"]}]}]}}  # fmt: skip


def test_parse_route_and_bad_response() -> None:
    pts = [(51.3, -0.5), (51.0, -0.2), (50.82, -0.14)]
    r = parse_route(body(encode(pts)))
    assert r.points == tuple(pts) and r.distance_km == 100.4 and r.duration_s == 4500
    assert r.roads == ("M23",)
    with pytest.raises(RoutingError):
        parse_route({"trip": {}})


async def test_client_errors() -> None:
    def down(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no")

    c = ValhallaClient("http://v", transport=httpx.MockTransport(down))
    with pytest.raises(RoutingError, match="unreachable"):
        await c.route((51, 0), (52, 0))
    c2 = ValhallaClient("http://v", transport=httpx.MockTransport(lambda _r: httpx.Response(400, text="bad")))
    with pytest.raises(RoutingError, match="HTTP 400"):
        await c2.route((51, 0), (52, 0))
    await c.aclose()
    await c2.aclose()


# ---- database: build a trip's corridor -------------------------------------------------

VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
NAMES = ["0003_nh_storage", "0006_places_trips", "0008_trip_routes"]
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
async def engine(schema: None) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(URL)  # type: ignore[arg-type]
    yield eng
    await eng.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_build_trips_creates_corridor_and_tags_events(engine: AsyncEngine) -> None:
    from datetime import UTC, datetime

    from app.integrations.nationalhighways.store import save_events
    from tests.test_nh_store import _event

    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO place (id, name, kind, lat, lon) VALUES "
                                "('home','Home','home',51.30,-0.50),('bri','Brighton','other',50.82,-0.14)"))  # fmt: skip
        await conn.execute(text("INSERT INTO trip (id, name, from_place, to_place) "
                                "VALUES ('home-to-bri','Home to Brighton','home','bri')"))  # fmt: skip
    # a stored speed event on the route's first stretch (M25 clockwise, lat 51.31)
    await save_events(engine, [_event("a")], [], "unplanned", datetime.now(UTC))

    route_pts = [(51.30, -0.499), (51.32, -0.499), (50.82, -0.14)]
    client = ValhallaClient(
        "http://v", transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=body(encode(route_pts))))
    )
    out = (await build_trips(engine, client, []))[0]
    await client.aclose()

    assert out["corridor"] == "trip-home-to-bri" and out["roads"] == ["M23"]
    assert out["distance_km"] == 100.4 and out["duration_min"] == 75
    corridors = await load_corridors(engine)
    assert [c.id for c in corridors] == ["trip-home-to-bri"]
    async with engine.connect() as conn:
        trip = (await conn.execute(text("SELECT corridor_id, roads, route_built_at FROM trip"))).one()
        assert trip[0] == "trip-home-to-bri" and trip[1] == ["M23"] and trip[2] is not None
