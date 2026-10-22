"""What the knowledge graph is made of, and how it is derived from what Postgres already holds.

Everything here is pure: rows in, nodes and relationships out. Nothing touches Neo4j or the
database, so the shape of the graph can be tested without either.

The graph owns nothing. Every node carries the id of the record it stands for, and the builder
recreates the lot from Postgres, so the graph can be dropped and rebuilt at any time. Properties
are kept to what a traversal needs for a decision or a label; the detail stays in Postgres.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

EARTH_KM = 6371.0088

# Labels, so a typo is a NameError rather than a quietly empty graph.
PLACE, TRIP, CORRIDOR, ROAD_EVENT, STATION, LOCATION, VEHICLE, TRACKER = (
    "Place", "Trip", "Corridor", "RoadEvent", "Station", "Location", "Vehicle", "BusTracker",
)  # fmt: skip
LABELS = (PLACE, TRIP, CORRIDOR, ROAD_EVENT, STATION, LOCATION, VEHICLE, TRACKER)

# Relationship types.
FROM, TO, USES, AFFECTS, ALONG, NEAR, DRIVES = "FROM", "TO", "USES", "AFFECTS", "ALONG", "NEAR", "DRIVES"
TYPES = (FROM, TO, USES, AFFECTS, ALONG, NEAR, DRIVES)


@dataclass(frozen=True)
class Node:
    label: str
    id: str
    props: dict[str, Any] = field(default_factory=dict)

    def key(self) -> tuple[str, str]:
        return self.label, self.id


@dataclass(frozen=True)
class Rel:
    type: str
    start: tuple[str, str]  # (label, id)
    end: tuple[str, str]
    props: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Graph:
    nodes: list[Node]
    rels: list[Rel]

    def counts(self) -> dict[str, int]:
        return {"nodes": len(self.nodes), "relationships": len(self.rels)}


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    p = math.pi / 180
    h = (
        math.sin((b[0] - a[0]) * p / 2) ** 2
        + math.cos(a[0] * p) * math.cos(b[0] * p) * math.sin((b[1] - a[1]) * p / 2) ** 2
    )
    return 2 * EARTH_KM * math.asin(math.sqrt(h))


def _point(row: dict[str, Any]) -> tuple[float, float] | None:
    lat, lon = row.get("lat"), row.get("lon")
    return None if lat is None or lon is None else (float(lat), float(lon))


def near_line(point: tuple[float, float], line: Sequence[tuple[float, float]], within_km: float) -> float | None:
    """How far a point is from the nearest waypoint of a line, or None if further than within_km.

    Distance to the waypoints rather than to the segments between them: corridors are stored as
    dense waypoints, so the difference is small, and this keeps the builder cheap and pure.
    """  # fmt: skip
    best = None
    for p in line:
        d = haversine_km(point, p)
        if best is None or d < best:
            best = d
    return best if best is not None and best <= within_km else None


def places(rows: Iterable[dict[str, Any]]) -> list[Node]:
    return [Node(PLACE, r["id"], {"name": r["name"], "kind": r.get("kind"),
                                  "postcode": r.get("postcode"), "lat": r.get("lat"), "lon": r.get("lon"),
                                  "origin": r.get("origin")})
            for r in rows]  # fmt: skip


def trips(rows: Iterable[dict[str, Any]]) -> tuple[list[Node], list[Rel]]:
    """A trip, and the places it runs between. A corridor is attached when one has been built."""
    nodes, rels = [], []
    for r in rows:
        tid = r["id"]
        nodes.append(Node(TRIP, tid, {"name": r["name"], "enabled": r.get("enabled", True)}))
        rels.append(Rel(FROM, (TRIP, tid), (PLACE, r["from_place"])))
        rels.append(Rel(TO, (TRIP, tid), (PLACE, r["to_place"])))
        if r.get("corridor_id"):
            rels.append(Rel(USES, (TRIP, tid), (CORRIDOR, r["corridor_id"])))
    return nodes, rels


def corridors(rows: Iterable[dict[str, Any]]) -> list[Node]:
    return [Node(CORRIDOR, r["id"], {"name": r["name"], "roads": list(r.get("roads") or []),
                                     "buffer_m": r.get("buffer_m")})
            for r in rows]  # fmt: skip


def road_events(rows: Iterable[dict[str, Any]], affects: Iterable[tuple[str, str]]) -> tuple[list[Node], list[Rel]]:
    """Open closures and speed limits, and the corridors they fall on.

    `affects` is (record_id, corridor_id) from nh_event_corridor: the one relationship Postgres
    already works out for itself.
    """  # fmt: skip
    nodes = [Node(ROAD_EVENT, r["record_id"], {"kind": r["kind"], "status": r.get("status"),
                                               "cause": r.get("cause"), "detail": r.get("detail"),
                                               "limit_mph": r.get("limit_mph"),
                                               "roads": list(r.get("roads") or [])})
             for r in rows]  # fmt: skip
    known = {n.id for n in nodes}
    rels = [Rel(AFFECTS, (ROAD_EVENT, e), (CORRIDOR, c)) for e, c in affects if e in known]
    return nodes, rels


def stations(rows: Iterable[dict[str, Any]]) -> list[Node]:
    return [Node(STATION, r["node_id"], {"name": r.get("trading_name"), "brand": r.get("brand_name"),
                                         "postcode": r.get("postcode"), "lat": r.get("lat"), "lon": r.get("lon"),
                                         "motorway": r.get("is_motorway"), "supermarket": r.get("is_supermarket")})
            for r in rows]  # fmt: skip


def locations(rows: Iterable[dict[str, Any]]) -> list[Node]:
    return [Node(LOCATION, r["id"], {"name": r["name"], "lat": r.get("lat"), "lon": r.get("lon")})
            for r in rows]  # fmt: skip


def vehicles(rows: Iterable[dict[str, Any]]) -> list[Node]:
    return [Node(VEHICLE, r["id"], {"name": r.get("name"), "kind": r.get("kind")}) for r in rows]


def trackers(rows: Iterable[dict[str, Any]]) -> list[Node]:
    out = []
    for r in rows:
        cfg = r.get("config") or {}
        out.append(Node(TRACKER, r["id"], {"line": cfg.get("line"), "enabled": r.get("enabled", True)}))
    return out


def near_places(
    things: Sequence[Node], place_nodes: Sequence[Node], within_km: float
) -> list[Rel]:
    """Anything with coordinates, tied to the places it is close to, with the distance."""
    out = []
    located = [(p, _point(p.props)) for p in place_nodes]
    for thing in things:
        a = _point(thing.props)
        if a is None:
            continue
        for place, b in located:
            if b is None:
                continue
            d = haversine_km(a, b)
            if d <= within_km:
                out.append(Rel(NEAR, thing.key(), place.key(), {"km": round(d, 3)}))
    return out


def along_corridors(
    station_nodes: Sequence[Node], corridor_rows: Iterable[dict[str, Any]], within_km: float
) -> list[Rel]:
    """Stations close enough to a corridor to be on the way."""
    out = []
    located = [(s, _point(s.props)) for s in station_nodes]
    for row in corridor_rows:
        line = [(float(p[0]), float(p[1])) for p in (row.get("centreline") or []) if len(p) >= 2]
        if not line:
            continue
        for station, point in located:
            if point is None:
                continue
            d = near_line(point, line, within_km)
            if d is not None:
                out.append(Rel(ALONG, station.key(), (CORRIDOR, row["id"]), {"km": round(d, 3)}))
    return out


def drives(vehicle_nodes: Sequence[Node], trip_nodes: Sequence[Node]) -> list[Rel]:
    """Every vehicle can make every trip. Stated once so a traversal can cost a trip per vehicle."""
    return [Rel(DRIVES, v.key(), t.key()) for v in vehicle_nodes for t in trip_nodes]


def build(
    *,
    place_rows: Sequence[dict[str, Any]],
    trip_rows: Sequence[dict[str, Any]],
    corridor_rows: Sequence[dict[str, Any]],
    event_rows: Sequence[dict[str, Any]],
    event_corridors: Sequence[tuple[str, str]],
    station_rows: Sequence[dict[str, Any]],
    location_rows: Sequence[dict[str, Any]],
    vehicle_rows: Sequence[dict[str, Any]],
    tracker_rows: Sequence[dict[str, Any]],
    near_km: float = 3.0,
    along_km: float = 2.0,
) -> Graph:
    """The whole graph, from one read of Postgres. Pure, so the shape is testable on its own."""
    place_nodes = places(place_rows)
    trip_nodes, trip_rels = trips(trip_rows)
    corridor_nodes = corridors(corridor_rows)
    event_nodes, event_rels = road_events(event_rows, event_corridors)
    station_nodes = stations(station_rows)
    location_nodes = locations(location_rows)
    vehicle_nodes = vehicles(vehicle_rows)
    tracker_nodes = trackers(tracker_rows)

    nodes = [*place_nodes, *trip_nodes, *corridor_nodes, *event_nodes, *station_nodes,
             *location_nodes, *vehicle_nodes, *tracker_nodes]  # fmt: skip
    rels = [
        *trip_rels,
        *event_rels,
        *near_places(station_nodes, place_nodes, near_km),
        *near_places(location_nodes, place_nodes, near_km),
        *along_corridors(station_nodes, corridor_rows, along_km),
        *drives(vehicle_nodes, trip_nodes),
    ]
    known = {n.key() for n in nodes}
    rels = [r for r in rels if r.start in known and r.end in known]  # never point at nothing
    return Graph(nodes, rels)
