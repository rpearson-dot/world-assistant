from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.geo import Located, PostcodeError, lookup_postcode, normalise_postcode
from app.main import app

URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
MIGRATIONS = ["0003_nh_storage", "0006_places_trips", "0008_trip_routes"]
TABLES = ["trip", "place", "nh_sign_message", "nh_sign", "nh_event_corridor", "nh_event",
          "nh_corridor"]  # fmt: skip
AUTH = {"Authorization": "Bearer s3cret"}

client = TestClient(app)


def test_normalise_postcode() -> None:
    assert normalise_postcode("sw1a2aa") == "SW1A 2AA"
    assert normalise_postcode(" sw1a  1aa ") == "SW1A 1AA"
    with pytest.raises(PostcodeError):
        normalise_postcode("not a postcode")


async def test_lookup_postcode_parses_result() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/postcodes/SW1A2AA"
        return httpx.Response(200, json={"status": 200, "result": {"latitude": 51.50, "longitude": -0.12}})

    got = await lookup_postcode("sw1a 2aa", transport=httpx.MockTransport(handler))
    assert got == Located("SW1A 2AA", 51.50, -0.12)


async def test_lookup_postcode_errors() -> None:
    not_found = httpx.MockTransport(lambda _r: httpx.Response(404, json={"status": 404}))
    with pytest.raises(PostcodeError, match="not found"):
        await lookup_postcode("SW1A 2AA", transport=not_found)
    no_coords = httpx.MockTransport(
        lambda _r: httpx.Response(200, json={"result": {"latitude": None, "longitude": None}})
    )
    with pytest.raises(PostcodeError, match="no coordinates"):
        await lookup_postcode("SW1A 2AA", transport=no_coords)


def test_places_page_loads_without_token() -> None:
    r = client.get("/places")
    assert r.status_code == 200
    assert "Places and trips" in r.text


def test_places_need_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "s3cret")
    assert client.get("/v1/places").status_code == 401
    assert client.put("/v1/places/home", json={"name": "Home", "postcode": "SW1A 2AA"}).status_code == 401


def test_place_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "s3cret")
    no_location = client.put("/v1/places/home", json={"name": "Home"}, headers=AUTH)
    assert no_location.status_code == 422
    bad_kind = client.put("/v1/places/home", json={"name": "H", "postcode": "SW1A 2AA", "kind": "x"}, headers=AUTH)
    assert bad_kind.status_code == 422
    outside = client.put("/v1/places/home", json={"name": "H", "lat": 10.0, "lon": 0.0}, headers=AUTH)
    assert outside.status_code == 422
    bad_slug = client.put("/v1/places/Bad_ID", json={"name": "H", "lat": 52.0, "lon": -1.0}, headers=AUTH)
    assert bad_slug.status_code == 422


@pytest.fixture
def schema() -> Iterator[None]:
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        for t in TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        with Operations.context(MigrationContext.configure(conn)):
            for name in MIGRATIONS:
                spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.upgrade()
    yield
    with sync.begin() as conn:
        for t in TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    sync.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_places_and_trips_roundtrip(
    schema: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_TOKEN", "s3cret")

    async def fake_lookup(postcode: str) -> Located:
        return Located(normalise_postcode(postcode), 51.50, -0.12)

    monkeypatch.setattr("app.places.lookup_postcode", fake_lookup)
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    app.state.engine = engine
    try:
        r = client.put("/v1/places/birmingham", json={"name": "Birmingham", "postcode": "sw1a2aa"}, headers=AUTH)
        assert r.status_code == 200
        assert r.json()["postcode"] == "SW1A 2AA"
        assert r.json()["lat"] == 51.50
        # explicit coordinates win over the postcode's (dragged pin)
        r = client.put("/v1/places/home", json={"name": "Home", "kind": "home", "lat": 51.3, "lon": -0.5}, headers=AUTH)
        assert r.json()["lat"] == 51.3
        names = [p["id"] for p in client.get("/v1/places", headers=AUTH).json()]
        assert names == ["birmingham", "home"]

        t = client.put("/v1/trips/home-birmingham", json={"from_place": "home", "to_place": "birmingham"}, headers=AUTH)
        assert t.status_code == 200
        assert t.json()["name"] == "home to birmingham"
        assert client.put("/v1/trips/x", json={"from_place": "home", "to_place": "home"}, headers=AUTH).status_code == 422
        assert client.put("/v1/trips/y", json={"from_place": "home", "to_place": "nowhere"}, headers=AUTH).status_code == 422
        # the same pair twice under another id is refused
        assert client.put("/v1/trips/dup", json={"from_place": "home", "to_place": "birmingham"}, headers=AUTH).status_code == 422
        assert len(client.get("/v1/trips", headers=AUTH).json()) == 1

        # deleting a place removes its trips
        assert client.delete("/v1/places/birmingham", headers=AUTH).status_code == 204
        assert client.get("/v1/trips", headers=AUTH).json() == []
        assert client.delete("/v1/places/birmingham", headers=AUTH).status_code == 404
    finally:
        await engine.dispose()
