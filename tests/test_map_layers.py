from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import map_layers
from app.main import app

URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
MIGRATIONS = ["0003_nh_storage", "0006_places_trips", "0007_fuel", "0015_pollen_grid", "0016_ha_location",
              "0017_ha_origin"]  # fmt: skip
TABLES = ["pollen_grid", "ha_location", "fuel_price_change", "fuel_price", "fuel_station", "trip", "place",
          "nh_sign_message", "nh_sign", "nh_event_corridor", "nh_event", "nh_corridor"]  # fmt: skip

HOME = (51.5074, -0.1278)  # the centre every test measures from
NEAR = (51.5100, -0.1300)  # about 0.4 km away
FAR = (52.4862, -1.8904)  # Birmingham: far outside any radius used here


def test_a_point_is_in_range_only_inside_the_radius() -> None:
    assert map_layers.within(HOME, 5.0, *NEAR)
    assert not map_layers.within(HOME, 5.0, *FAR)
    assert map_layers.within(None, 5.0, *FAR)  # no centre: everything
    assert not map_layers.within(HOME, 5.0, None, None)


def test_a_line_is_in_range_if_any_point_is() -> None:
    assert map_layers.line_within(HOME, 5.0, [FAR, NEAR])  # drawn whole, not cut in half
    assert not map_layers.line_within(HOME, 5.0, [FAR])
    assert not map_layers.line_within(HOME, 5.0, [])


def test_pollen_level_reads_the_stored_forecast() -> None:
    assert map_layers.pollen_level({"allergy_risk_label": ["high", "low"]}) == ("high", 3)
    assert map_layers.pollen_level({"label": ["none"]}) == ("none", 0)
    assert map_layers.pollen_level({"allergy_risk_label": []}) == (None, None)
    assert map_layers.pollen_level({"allergy_risk_label": ["nonsense"]}) == (None, None)
    assert map_layers.pollen_level(None) == (None, None)


def test_geojson_coordinates_are_lon_then_lat() -> None:
    """GeoJSON wants [lon, lat]; everything else in the project stores (lat, lon)."""
    assert map_layers._point(51.5, -0.1, {"a": 1})["geometry"]["coordinates"] == [-0.1, 51.5]
    assert map_layers._line([(51.5, -0.1), (51.6, -0.2)], {})["geometry"]["coordinates"] == [
        [-0.1, 51.5], [-0.2, 51.6]]  # fmt: skip


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
        conn.execute(text("INSERT INTO place (id, name, kind, lat, lon) "
                          "VALUES ('home', 'Home', 'home', :la, :lo), ('away', 'Away', 'other', :fa, :fo)"),
                     {"la": HOME[0], "lo": HOME[1], "fa": FAR[0], "fo": FAR[1]})  # fmt: skip
        conn.execute(text("INSERT INTO ha_location (id, name, lat, lon) VALUES ('phone', 'Phone', :la, :lo)"),
                     {"la": NEAR[0], "lo": NEAR[1]})  # fmt: skip
        conn.execute(text("INSERT INTO nh_corridor (id, name, centreline, roads) "
                          "VALUES ('m1', 'The long way', CAST(:c AS jsonb), '{M1}')"),
                     {"c": f"[[{FAR[0]}, {FAR[1]}], [{NEAR[0]}, {NEAR[1]}]]"})  # fmt: skip
        conn.execute(text("INSERT INTO nh_event (record_id, scope, kind, segments) VALUES "
                          "('e1', 'unplanned', 'closure', CAST(:s AS jsonb)), "
                          "('e2', 'unplanned', 'closure', CAST(:f AS jsonb))"),
                     {"s": f'[{{"road": "A3", "points": [[{NEAR[0]}, {NEAR[1]}]]}}]',
                      "f": f'[{{"road": "M6", "points": [[{FAR[0]}, {FAR[1]}]]}}]'})  # fmt: skip
        conn.execute(text("INSERT INTO nh_sign (sign_id, road, lat, lon, working) "
                          "VALUES ('s1', 'A3', :la, :lo, true)"), {"la": NEAR[0], "lo": NEAR[1]})  # fmt: skip
        conn.execute(text("INSERT INTO fuel_station (node_id, trading_name, lat, lon, coords_ok) VALUES "
                          "('f1', 'Near garage', :la, :lo, true), ('f2', 'Far garage', :fa, :fo, true), "
                          "('f3', 'No coordinates', NULL, NULL, false)"),
                     {"la": NEAR[0], "lo": NEAR[1], "fa": FAR[0], "fo": FAR[1]})  # fmt: skip
        conn.execute(text("INSERT INTO fuel_price (node_id, fuel_type, pence) VALUES ('f1', 'E10', 139.9)"))
        conn.execute(text("INSERT INTO pollen_grid (lat, lon, forecast, fingerprint) "
                          "VALUES (:la, :lo, CAST(:f AS jsonb), 'x')"),
                     {"la": NEAR[0], "lo": NEAR[1], "f": '{"allergy_risk_label": ["low"]}'})  # fmt: skip
    yield
    with sync.begin() as conn:
        for t in TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    sync.dispose()


ALL = "places,locations,roads,road_events,signs,fuel,pollen"


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_every_layer_is_served_and_cut_to_the_radius(
    schema: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        app.state.engine = engine
        c = TestClient(app)
        h = {"Authorization": "Bearer tok"}
        assert c.get(f"/v1/map/geojson?layers={ALL}").status_code == 401  # a token is needed

        cat = c.get("/v1/map/layers", headers=h).json()["layers"]
        assert {x["id"] for x in cat} == set(map_layers.LAYERS)
        assert next(x for x in cat if x["id"] == "fuel")["features"] == 2  # the one without coordinates is out

        r = c.get(f"/v1/map/geojson?layers={ALL}&radius_km=5", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["centre"] == {"name": "Home", "lat": HOME[0], "lon": HOME[1], "radius_km": 5.0}
        lay = body["layers"]
        assert all(v["type"] == "FeatureCollection" for v in lay.values())
        names = [f["properties"]["name"] for f in lay["places"]["features"]]
        assert names == ["Home"]  # 'Away' is out of range
        assert [f["properties"]["name"] for f in lay["locations"]["features"]] == ["Phone"]
        assert [f["properties"]["road"] for f in lay["road_events"]["features"]] == ["A3"]
        assert [f["properties"]["id"] for f in lay["signs"]["features"]] == ["s1"]
        assert [f["properties"]["pence"] for f in lay["fuel"]["features"]] == [139.9]
        assert [f["properties"]["label"] for f in lay["pollen"]["features"]] == ["low"]
        # the corridor runs from far away to nearby: in range, and drawn in full
        roads = lay["roads"]["features"]
        assert len(roads) == 1 and len(roads[0]["geometry"]["coordinates"]) == 2
        assert roads[0]["properties"]["roads"] == ["M1"]

        wide = c.get(f"/v1/map/geojson?layers={ALL}&everywhere=true", headers=h).json()
        assert wide["centre"] is None
        assert len(wide["layers"]["places"]["features"]) == 2
        assert len(wide["layers"]["fuel"]["features"]) == 2

        spot = c.get("/v1/map/geojson?layers=places&lat=52.4862&lon=-1.8904&radius_km=3", headers=h).json()
        assert [f["properties"]["name"] for f in spot["layers"]["places"]["features"]] == ["Away"]

        assert c.get("/v1/map/geojson?layers=nope", headers=h).status_code == 422
        assert c.get("/v1/map/geojson?layers=fuel&fuel=sunflower+oil", headers=h).status_code == 422
    finally:
        await engine.dispose()
