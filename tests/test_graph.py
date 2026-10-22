"""The knowledge graph: what it derives, what it writes, and how it behaves with no Neo4j.

There is no Neo4j in CI, so the writer is driven against a fake session that records every
statement. That proves the order, the grouping and the parameters; it does not prove the Cypher
itself parses, which only a real server can.
"""

from __future__ import annotations

from typing import Any, Self

import pytest
from fastapi.testclient import TestClient

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
    "vehicle_rows": [{"id": "leaf", "name": "Leaf", "kind": "electric"}],
    "tracker_rows": [{"id": "213", "config": {"line": "213"}, "enabled": True}],
}


def built() -> model.Graph:
    return model.build(**ROWS, near_km=3.0, along_km=2.0)


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


def test_trips_join_their_places_and_corridor() -> None:
    rels = {(r.type, r.start[1], r.end[1]) for r in built().rels}
    assert ("FROM", "commute", "home") in rels
    assert ("TO", "commute", "work") in rels
    assert ("USES", "commute", "a3") in rels
    assert not any(r[0] == "USES" and r[1] == "orphan" for r in rels)  # no corridor built yet


def test_a_closure_reaches_the_corridor_it_sits_on() -> None:
    rels = [r for r in built().rels if r.type == "AFFECTS"]
    assert [(r.start[1], r.end[1]) for r in rels] == [("e1", "a3")]  # the ghost event is dropped


def test_near_uses_distance_not_hope() -> None:
    near = [r for r in built().rels if r.type == "NEAR"]
    pairs = {(r.start[1], r.end[1]) for r in near}
    assert ("f1", "home") in pairs  # 0.4 km away
    assert ("phone", "home") in pairs
    assert ("f2", "home") not in pairs  # Birmingham
    assert ("f3", "home") not in pairs  # no coordinates at all
    assert all(0 <= r.props["km"] <= 3.0 for r in near)


def test_a_station_on_the_way_is_along_the_corridor() -> None:
    along = {(r.start[1], r.end[1]) for r in built().rels if r.type == "ALONG"}
    assert ("f1", "a3") in along and ("f2", "a3") in along  # the corridor runs to Birmingham
    assert ("f3", "a3") not in along


def test_no_relationship_points_at_a_node_that_is_not_there() -> None:
    g = built()
    keys = {n.key() for n in g.nodes}
    assert all(r.start in keys and r.end in keys for r in g.rels)
    assert all(r.type in model.TYPES for r in g.rels)


def test_an_empty_database_makes_an_empty_graph() -> None:
    empty = {k: [] for k in ROWS}
    g = model.build(**empty)
    assert g.counts() == {"nodes": 0, "relationships": 0}


def test_near_line_measures_to_the_closest_point() -> None:
    line = [HOME, FAR]
    assert model.near_line(NEAR, line, 1.0) == pytest.approx(0.4, abs=0.2)
    assert model.near_line(NEAR, line, 0.1) is None
    assert model.near_line(NEAR, [], 50.0) is None


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
