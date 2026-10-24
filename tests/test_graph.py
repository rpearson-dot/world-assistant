"""The knowledge graph: what it derives, what it writes, and how it behaves with no Neo4j.

There is no Neo4j in CI, so the writer is driven against a fake session that records every
statement. That proves the order, the grouping and the parameters; it does not prove the Cypher
itself parses, which only a real server can.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Self

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.graph import model, store
from app.main import app

HOME = (51.5074, -0.1278)
NEAR = (51.5100, -0.1300)  # ~0.4 km from home
FAR = (52.4862, -1.8904)  # Birmingham

ROWS: dict[str, Any] = {
    "place_rows": [
        {"id": "home", "name": "Home", "kind": "home", "postcode": "SW1", "lat": HOME[0], "lon": HOME[1]},
        {"id": "work", "name": "Work", "kind": "work", "lat": FAR[0], "lon": FAR[1]},
    ],
    "trip_rows": [
        {"id": "commute", "name": "Commute", "from_place": "home", "to_place": "work",
         "enabled": True, "corridor_id": "a3"},
        {"id": "orphan", "name": "No corridor yet", "from_place": "work", "to_place": "home",
         "enabled": True, "corridor_id": None},
    ],
    "corridor_rows": [
        {"id": "a3", "name": "A3 corridor", "buffer_m": 500, "roads": ["A3"],
         "centreline": [[HOME[0], HOME[1]], [NEAR[0], NEAR[1]], [FAR[0], FAR[1]]]},
    ],
    "event_rows": [
        {"record_id": "e1", "kind": "closure", "status": "active", "cause": "Roadworks",
         "detail": None, "limit_mph": None, "roads": ["A3"]},
        {"record_id": "e2", "kind": "speed", "status": "active", "cause": None,
         "detail": "mandatory", "limit_mph": 50, "roads": ["M1"]},
    ],
    "event_corridors": [("e1", "a3"), ("ghost", "a3")],  # the ghost event is not in event_rows
    "station_rows": [
        {"node_id": "f1", "trading_name": "Near garage", "brand_name": "Shell", "postcode": "SW1",
         "lat": NEAR[0], "lon": NEAR[1], "is_motorway": False, "is_supermarket": False},
        {"node_id": "f2", "trading_name": "Far garage", "brand_name": "BP", "postcode": "B1",
         "lat": FAR[0], "lon": FAR[1], "is_motorway": False, "is_supermarket": False},
        {"node_id": "f3", "trading_name": "No coordinates", "brand_name": None, "postcode": None,
         "lat": None, "lon": None, "is_motorway": False, "is_supermarket": False},
    ],
    "location_rows": [{"id": "phone", "name": "Phone", "lat": NEAR[0], "lon": NEAR[1]}],
    # Proximity is measured by PostGIS in build.read(); the model is handed the answers.
    "near_pairs": [("Station", "f1", "home", 0.327), ("Location", "phone", "home", 0.327)],
    "along_pairs": [("f1", "a3", 0.0), ("f2", "a3", 0.0)],
    "vehicle_rows": [{"id": "leaf", "name": "Leaf", "kind": "electric"}],
    "tracker_rows": [{"id": "213", "config": {"line": "213"}, "enabled": True}],
    # Stops the tracker watches, positioned by NaPTAN. "ghost" is a stop NaPTAN has no record of.
    "bus_stop_rows": [{"id": "490001346C", "name": "Home Road", "indicator": "Stop A",
                       "lat": NEAR[0], "lon": NEAR[1]}],
    "calls_at": [("213", "490001346C"), ("213", "ghost")],
    # a live warning over home and across the A3, and one nobody has a place or corridor in
    "flood_rows": [{"id": "w1", "name": "Thames", "level": 2, "severity": "Flood Warning", "county": "London",
                    "river_or_sea": "River Thames"},
                   {"id": "w2", "name": "Elsewhere", "level": 3, "severity": "Flood Alert"}],
    "flood_covers": [("w1", "home"), ("w1", "ghost-place")],
    "flood_crosses": [("w1", "a3")],
    "disruption_rows": [{"id": "d1", "category": "Works", "severity": "Moderate", "scope": "planned",
                         "status": "Active", "location": "High Road, New Malden", "has_closures": False}],
    "disruption_corridors": [("d1", "a3"), ("ghost", "a3")],
    "local_event_rows": [{"id": "skiddle:1", "source": "skiddle", "name": "Gig", "day": "2026-11-01",
                          "type": "LIVE", "venue": "Hall", "town": "Kingston", "lat": NEAR[0], "lon": NEAR[1]}],
    "area_rows": [{"id": "district:K", "kind": "district", "name": "Kingston", "code": "E1"},
                  {"id": "county:L", "kind": "county", "name": "Greater London", "code": "E2"},
                  {"id": "region:SE", "kind": "region", "name": "South East", "code": "E3"},
                  {"id": "district:Empty", "kind": "district", "name": "Nobody lives here", "code": "E4"}],
    "inside_pairs": [("Place", "home", "district:K"), ("Disruption", "d1", "district:K"),
                     ("LocalEvent", "skiddle:1", "district:K"), ("Place", "ghost", "district:K")],
    "area_parents": [("district:K", "county:L"), ("county:L", "region:SE"), ("district:Empty", "county:L")],
}


def built() -> model.Graph:
    return model.build(**ROWS)


# --- what the graph is made of ------------------------------------------------------------------


def test_every_record_becomes_a_node() -> None:
    g = built()
    by_label: dict[str, list[str]] = {}
    for n in g.nodes:
        by_label.setdefault(n.label, []).append(n.id)
    assert sorted(by_label["Place"]) == ["home", "work"]
    assert sorted(by_label["Trip"]) == ["commute", "orphan"]
    assert sorted(by_label["Station"]) == ["f1", "f2", "f3"]  # the one without coordinates too
    assert by_label["Vehicle"] == ["leaf"] and by_label["BusTracker"] == ["213"]
    assert all(n.label in model.LABELS for n in g.nodes)


def test_a_tracker_calls_at_the_stops_naptan_could_place() -> None:
    g = built()
    assert [n.id for n in g.nodes if n.label == "BusStop"] == ["490001346C"]
    calls = {(r.start[1], r.end[1]) for r in g.rels if r.type == "CALLS_AT"}
    assert calls == {("213", "490001346C")}  # 'ghost' has no position, so no node and no relationship


def test_a_flood_warning_covers_the_places_and_crosses_the_corridors_postgis_found() -> None:
    g = built()
    assert sorted(n.id for n in g.nodes if n.label == "FloodWarning") == ["w1", "w2"]
    covers = {(r.start[1], r.end[1]) for r in g.rels if r.type == "COVERS"}
    crosses = {(r.start[1], r.end[1]) for r in g.rels if r.type == "CROSSES"}
    assert covers == {("w1", "home")}  # 'ghost-place' is not a node, so no dangling relationship
    assert crosses == {("w1", "a3")}


def test_areas_keep_only_what_something_is_in_and_the_areas_above_those() -> None:
    g = built()
    assert sorted(n.id for n in g.nodes if n.label == "Area") == ["county:L", "district:K", "region:SE"]
    ins = {(r.start, r.end[1]) for r in g.rels if r.type == "IN"}
    assert (("Place", "home"), "district:K") in ins
    assert (("Disruption", "d1"), "district:K") in ins and (("LocalEvent", "skiddle:1"), "district:K") in ins
    assert (("Area", "district:K"), "county:L") in ins and (("Area", "county:L"), "region:SE") in ins
    assert not any(a == "district:Empty" for _, a in ins)
    assert not any(s == ("Place", "ghost") for s, _ in ins)


def test_disruptions_and_events_become_nodes() -> None:
    g = built()
    assert [n.id for n in g.nodes if n.label == "Disruption"] == ["d1"]
    assert [n.props["name"] for n in g.nodes if n.label == "LocalEvent"] == ["Gig"]
    affects = {(r.start[1], r.end[1]) for r in g.rels if r.type == "AFFECTS"}
    assert ("d1", "a3") in affects and ("ghost", "a3") not in affects


def test_trips_join_their_places_and_corridor() -> None:
    rels = {(r.type, r.start[1], r.end[1]) for r in built().rels}
    assert ("FROM", "commute", "home") in rels
    assert ("TO", "commute", "work") in rels
    assert ("USES", "commute", "a3") in rels
    assert not any(r[0] == "USES" and r[1] == "orphan" for r in rels)  # no corridor built yet


def test_a_closure_reaches_the_corridor_it_sits_on() -> None:
    rels = [r for r in built().rels if r.type == "AFFECTS" and r.start[0] == "RoadEvent"]
    assert [(r.start[1], r.end[1]) for r in rels] == [("e1", "a3")]  # the ghost event is dropped


def test_near_carries_the_measured_distance() -> None:
    near = [r for r in built().rels if r.type == "NEAR"]
    pairs = {(r.start[1], r.end[1]) for r in near}
    assert ("f1", "home") in pairs and ("phone", "home") in pairs
    assert ("f2", "home") not in pairs  # Birmingham: PostGIS did not return it
    assert all(r.props["km"] == pytest.approx(0.327) for r in near)


def test_a_station_on_the_way_is_along_the_corridor() -> None:
    along = {(r.start[1], r.end[1]) for r in built().rels if r.type == "ALONG"}
    assert ("f1", "a3") in along and ("f2", "a3") in along  # the corridor runs to Birmingham
    assert ("f3", "a3") not in along  # no coordinates, so PostGIS never matched it


def test_no_relationship_points_at_a_node_that_is_not_there() -> None:
    g = built()
    keys = {n.key() for n in g.nodes}
    assert all(r.start in keys and r.end in keys for r in g.rels)
    assert all(r.type in model.TYPES for r in g.rels)


def test_an_empty_database_makes_an_empty_graph() -> None:
    empty = {k: [] for k in ROWS}
    g = model.build(**empty)
    assert g.counts() == {"nodes": 0, "relationships": 0}


def test_a_relationship_with_no_measured_pairs_simply_is_not_there() -> None:
    bare = {**ROWS, "near_pairs": [], "along_pairs": []}
    g = model.build(**bare)
    assert not [r for r in g.rels if r.type in ("NEAR", "ALONG")]
    assert [r for r in g.rels if r.type == "FROM"]  # the rest is unaffected


# --- what gets written --------------------------------------------------------------------------


class FakeSession:
    """Records what would have been run, so the writer can be tested without a server."""

    def __init__(self, log: list[tuple[str, dict[str, Any]]]) -> None:
        self.log = log

    async def run(self, statement: str, **params: Any) -> Any:
        self.log.append((statement, params))
        return _Empty()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None


class _Empty:
    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


async def test_the_writer_creates_constraints_then_nodes_then_relationships_then_sweeps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(store, "session", lambda: FakeSession(log))
    counts = await store.write(built(), "BUILD1")
    assert counts == built().counts()

    statements = [s for s, _ in log]
    first_merge = next(i for i, s in enumerate(statements) if s.startswith("UNWIND"))
    assert all(s.startswith("CREATE CONSTRAINT") for s in statements[:first_merge])
    assert statements[-2:] == [store.SWEEP_RELS, store.SWEEP_NODES]
    # nodes before relationships: a relationship cannot MATCH a node that is not there yet
    last_node = max(i for i, s in enumerate(statements) if "MERGE (n:" in s)
    first_rel = min(i for i, s in enumerate(statements) if "MERGE (a)-" in s)
    assert last_node < first_rel
    assert all(p.get("built") == "BUILD1" for s, p in log if not s.startswith("CREATE CONSTRAINT"))


async def test_every_written_row_carries_its_id_and_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(store, "session", lambda: FakeSession(log))
    await store.write(built(), "BUILD1")
    places = next(p["rows"] for s, p in log if "MERGE (n:Place" in s)
    home = next(r for r in places if r["id"] == "home")
    assert home["props"]["name"] == "Home" and home["props"]["lat"] == HOME[0]
    assert "origin" not in home["props"]  # None is left out rather than written as null
    near = next(p["rows"] for s, p in log if "MERGE (a)-[r:NEAR]" in s)
    assert all("km" in r["props"] for r in near)


def test_statements_refuse_a_label_or_type_that_is_not_in_the_vocabulary() -> None:
    assert "MERGE (n:Place {id: row.id})" in store.node_statement("Place")
    assert "MERGE (a)-[r:FROM]->(b)" in store.rel_statement("FROM", "Trip", "Place")
    for bad in ("Robert'); DROP", "place", "Unknown"):
        with pytest.raises(ValueError):
            store.node_statement(bad)
    with pytest.raises(ValueError):
        store.rel_statement("KNOWS", "Trip", "Place")
    with pytest.raises(ValueError):
        store.rel_statement("FROM", "Trip", "Nope")


def test_the_sweep_catches_rows_from_before_this_scheme() -> None:
    """A node or relationship with no `built` at all must still be swept, not left behind."""
    assert "IS NULL" in store.SWEEP_RELS and "IS NULL" in store.SWEEP_NODES


def test_grouping_puts_each_label_and_each_relationship_shape_in_one_batch() -> None:
    g = built()
    nodes = store.group_nodes(g.nodes)
    assert set(nodes) <= set(model.LABELS)
    assert sum(len(v) for v in nodes.values()) == len(g.nodes)
    rels = store.group_rels(g.rels)
    assert all(t in model.TYPES for t, _, _ in rels)
    assert sum(len(v) for v in rels.values()) == len(g.rels)


# --- the API ------------------------------------------------------------------------------------


def test_the_graph_endpoints_say_plainly_when_there_is_no_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """Switched off is not the same as "nothing found", and must not read like it."""
    monkeypatch.setenv("API_TOKEN", "tok")
    c, h = TestClient(app), {"Authorization": "Bearer tok"}
    for path in ("/v1/graph/counts", "/v1/graph/neighbours?id=home", "/v1/graph/disruption?place=home"):
        r = c.get(path, headers=h)
        assert r.status_code == 503
        assert "switched off" in r.json()["detail"]
    assert c.get("/v1/graph/counts").status_code == 401  # still behind the token


def test_about_describes_the_vocabulary_without_needing_the_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    body = TestClient(app).get("/v1/graph/about", headers={"Authorization": "Bearer tok"}).json()
    assert set(body["labels"]) == set(model.LABELS)
    assert set(body["relationships"]) == set(model.TYPES)


def test_an_unknown_label_is_refused_before_the_graph_is_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    c, h = TestClient(app), {"Authorization": "Bearer tok"}
    assert c.get("/v1/graph/neighbours?id=home&label=Nonsense", headers=h).status_code == 422


# --- the proximity SQL, against a real PostGIS ---------------------------------------------------

URL = os.environ.get("WA_TEST_DATABASE_URL")
VERSIONS = Path(__file__).parent.parent / "migrations" / "versions"
GEO_MIGRATIONS = ["0003_nh_storage", "0006_places_trips", "0007_fuel", "0009_vehicle",
                  "0011_bus_tracking", "0016_ha_location", "0017_ha_origin", "0018_geography",
                  "0020_naptan", "0021_flood", "0025_tfl_road", "0027_boundary_usrn", "0028_local_event",
                  "0030_tfl_station"]  # fmt: skip
GEO_TABLES = ["tfl_station_alert", "tfl_station_part", "tfl_station", "local_event", "usrn", "boundary", "tfl_road_disruption", "flood_warning", "flood_area", "naptan_stop", "bus_timetable", "bus_target_hit", "bus_target_day", "bus_anomaly", "bus_segment_obs",
              "bus_stop", "bus_tracker", "vehicle", "fuel_price_change", "fuel_price", "fuel_station",
              "ha_location", "trip", "place", "nh_sign_message", "nh_sign", "nh_event_corridor",
              "nh_event", "nh_corridor"]  # fmt: skip


@pytest.fixture
def geo_schema() -> Iterator[None]:
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        for t in GEO_TABLES:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        with Operations.context(MigrationContext.configure(conn)):
            for name in GEO_MIGRATIONS:
                spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.upgrade()
        conn.execute(sa.text(
            "INSERT INTO place (id, name, lat, lon) VALUES ('home', 'Home', :la, :lo), "
            "('brum', 'Brum', :fa, :fo)"), {"la": HOME[0], "lo": HOME[1], "fa": FAR[0], "fo": FAR[1]})
        conn.execute(sa.text(
            "INSERT INTO ha_location (id, name, lat, lon) VALUES ('phone', 'Phone', :la, :lo)"),
            {"la": NEAR[0], "lo": NEAR[1]})
        conn.execute(sa.text(
            "INSERT INTO fuel_station (node_id, trading_name, lat, lon, coords_ok) VALUES "
            "('f1', 'Near', :la, :lo, true), ('f2', 'Far', :fa, :fo, true), "
            "('f3', 'Nowhere', NULL, NULL, false)"),
            {"la": NEAR[0], "lo": NEAR[1], "fa": FAR[0], "fo": FAR[1]})
        conn.execute(sa.text(
            "INSERT INTO nh_corridor (id, name, centreline) VALUES ('a3', 'A3', CAST(:c AS jsonb)), "
            "('stub', 'One point only', CAST(:s AS jsonb))"),
            {"c": f"[[{HOME[0]}, {HOME[1]}], [{FAR[0]}, {FAR[1]}]]", "s": f"[[{HOME[0]}, {HOME[1]}]]"})
        # a tracker watching one stop; NaPTAN also knows a second stop nobody watches
        conn.execute(sa.text("INSERT INTO bus_tracker (id, config) VALUES ('213', '{}')"))
        conn.execute(sa.text("INSERT INTO bus_stop (tracker, direction, pos, sid, name) "
                             "VALUES ('213', 'outbound', 0, 'watched', 'Watched stop')"))
        conn.execute(sa.text(
            "INSERT INTO naptan_stop (atco, name, stop_type, kind, lat, lon, seen) VALUES "
            "('watched', 'Watched stop', 'BCT', 'bus_stop', :la, :lo, now()), "
            "('unwatched', 'Unwatched stop', 'BCT', 'bus_stop', :la, :lo, now())"),
            {"la": NEAR[0], "lo": NEAR[1]})
        # a live warning whose area surrounds home and runs on to Birmingham (so it crosses the A3), and
        # an ended one that must stay out of the graph
        conn.execute(sa.text("INSERT INTO flood_area (notation, label, geom) VALUES "
                             "('wide', 'Wide area', ST_GeogFromText(:wkt)), ('gone', 'Ended', ST_GeogFromText(:wkt))"),
                     {"wkt": "POLYGON((0 51.4, -1.95 51.4, -1.95 52.6, 0 52.6, 0 51.4))"})
        # a district inside a county, both round home; a disruption and a (future) event at the near point
        for bid, kind, ring in (("district:K", "district", "-0.2 51.4, -0.1 51.4, -0.1 51.6, -0.2 51.6, -0.2 51.4"),
                                ("county:L", "county", "-0.5 51.2, 0.3 51.2, 0.3 51.8, -0.5 51.8, -0.5 51.2")):
            conn.execute(sa.text("INSERT INTO boundary (id, kind, name, seen, geom) VALUES "
                                 "(:i, :k, :i, now(), ST_Multi(ST_GeomFromText(:w, 4326)))"),
                         {"i": bid, "k": kind, "w": f"POLYGON(({ring}))"})
        conn.execute(sa.text("INSERT INTO tfl_road_disruption (id, scope, seen, geom) VALUES "
                             "('d1', 'planned', now(), ST_SetSRID(ST_MakePoint(:lo, :la), 4326)::geography)"),
                     {"la": NEAR[0], "lo": NEAR[1]})
        conn.execute(sa.text("INSERT INTO local_event (id, source, name, day, lat, lon, seen) VALUES "
                             "('skiddle:1', 'skiddle', 'Gig', CURRENT_DATE + 3, :la, :lo, now()), "
                             "('skiddle:old', 'skiddle', 'Past', CURRENT_DATE - 3, :la, :lo, now())"),
                     {"la": NEAR[0], "lo": NEAR[1]})
        conn.execute(sa.text("INSERT INTO flood_warning (id, area, severity_level, severity, seen) VALUES "
                             "('wide', 'wide', 2, 'Flood Warning', now()), ('gone', 'gone', 4, 'Gone', now())"))
    yield
    with sync.begin() as conn:
        for t in GEO_TABLES:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    sync.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_the_generated_point_tracks_lat_and_lon_by_itself(geo_schema: None) -> None:
    """geog is GENERATED, so it cannot drift and nothing has to remember to write it."""
    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        async with engine.begin() as conn:
            empty = await conn.execute(sa.text(
                "SELECT geog IS NULL FROM fuel_station WHERE node_id = 'f3'"))
            assert empty.scalar() is True  # no coordinates, no point in the Atlantic
            await conn.execute(sa.text("UPDATE place SET lat = :la, lon = :lo WHERE id = 'brum'"),
                               {"la": NEAR[0], "lo": NEAR[1]})
            moved = await conn.execute(sa.text(
                "SELECT ST_Distance(geog, ST_SetSRID(ST_MakePoint(:lo, :la), 4326)::geography) "
                "FROM place WHERE id = 'brum'"), {"la": NEAR[0], "lo": NEAR[1]})
            assert moved.scalar() == pytest.approx(0.0, abs=0.001)  # followed the update
    finally:
        await engine.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_postgis_finds_what_is_near_and_what_is_on_the_way(geo_schema: None) -> None:
    from app.graph.build import read

    engine = create_async_engine(URL)  # type: ignore[arg-type]
    try:
        rows = await read(engine, near_km=3.0, along_km=2.0)
        near = {(label, thing, place) for label, thing, place, _ in rows["near_pairs"]}
        assert ("Station", "f1", "home") in near  # 0.4 km away
        assert ("Location", "phone", "home") in near
        assert ("Station", "f2", "home") not in near  # Birmingham
        assert not any(t == "f3" for _, t, _ in near)  # no coordinates
        assert all(0 <= km <= 3.0 for *_, km in rows["near_pairs"])
        assert ("BusStop", "watched", "home") in near  # a tracked stop is placed by NaPTAN
        assert not any(t == "unwatched" for _, t, _ in near)  # the register stays out of the graph
        assert [r["id"] for r in rows["bus_stop_rows"]] == ["watched"]
        assert rows["calls_at"] == [("213", "watched")]
        assert [r["id"] for r in rows["flood_rows"]] == ["wide"]  # the ended warning is not live
        assert ("wide", "home") in rows["flood_covers"] and ("wide", "brum") in rows["flood_covers"]
        assert rows["flood_crosses"] == [("wide", "a3")]

        assert [r["id"] for r in rows["disruption_rows"]] == ["d1"]
        assert [r["id"] for r in rows["local_event_rows"]] == ["skiddle:1"]  # the past one is gone
        assert ("LocalEvent", "skiddle:1", "home") in near and ("Disruption", "d1", "home") in near
        inside = {(label, thing, area) for label, thing, area in rows["inside_pairs"]}
        assert ("Place", "home", "district:K") in inside and ("Place", "home", "county:L") in inside
        assert ("Place", "brum", "district:K") not in inside
        assert ("district:K", "county:L") in rows["area_parents"]
        assert ("d1", "a3") in rows["disruption_corridors"]

        along = {(s, c) for s, c, _ in rows["along_pairs"]}
        assert ("f1", "a3") in along and ("f2", "a3") in along  # the corridor runs to Birmingham
        assert not any(c == "stub" for _, c in along)  # a one-point line is not a line

        # and the whole thing assembles
        graph = model.build(**rows)
        assert graph.counts()["nodes"] > 0
        assert any(r.type == "NEAR" for r in graph.rels)
    finally:
        await engine.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
async def test_the_census_geocoder_is_dropped_and_stops_shadowing_our_place() -> None:
    """postgis/postgis ships postgis_tiger_geocoder, whose `tiger` schema holds its own `place`."""
    import importlib.util as iu

    spec = iu.spec_from_file_location("m0019", VERSIONS / "0019_drop_tiger_geocoder.py")
    assert spec and spec.loader
    m0019 = iu.module_from_spec(spec)
    spec.loader.exec_module(m0019)

    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    try:
        with sync.begin() as conn:
            conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS postgis_tiger_geocoder CASCADE"))
        with sync.begin() as conn:
            assert conn.execute(sa.text(
                "SELECT count(*) FROM pg_tables WHERE schemaname IN ('tiger','tiger_data')")).scalar() > 0
            with Operations.context(MigrationContext.configure(conn)):
                m0019.upgrade()
        with sync.begin() as conn:
            assert conn.execute(sa.text(
                "SELECT count(*) FROM pg_tables WHERE schemaname IN ('tiger','tiger_data')")).scalar() == 0
            assert conn.execute(sa.text(
                "SELECT count(*) FROM pg_extension WHERE extname = 'postgis_tiger_geocoder'")).scalar() == 0
            # postgis itself must survive: the geography columns depend on it
            assert conn.execute(sa.text(
                "SELECT count(*) FROM pg_extension WHERE extname = 'postgis'")).scalar() == 1
    finally:
        sync.dispose()
