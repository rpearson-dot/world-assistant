"""What the knowledge graph is made of, and how it is derived from what Postgres already holds.

Everything here is pure: rows in, nodes and relationships out. Nothing touches Neo4j or the
database, so the shape of the graph can be tested without either.

The graph owns nothing. Every node carries the id of the record it stands for, and the builder
recreates the lot from Postgres, so the graph can be dropped and rebuilt at any time. Properties
are kept to what a traversal needs for a decision or a label; the detail stays in Postgres.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

# Labels, so a typo is a NameError rather than a quietly empty graph.
PLACE, TRIP, CORRIDOR, ROAD_EVENT, STATION, LOCATION, VEHICLE, TRACKER, BUS_STOP, FLOOD = (
    "Place", "Trip", "Corridor", "RoadEvent", "Station", "Location", "Vehicle", "BusTracker", "BusStop",
    "FloodWarning",
)  # fmt: skip
AREA, DISRUPTION, LOCAL_EVENT = "Area", "Disruption", "LocalEvent"
LABELS = (PLACE, TRIP, CORRIDOR, ROAD_EVENT, STATION, LOCATION, VEHICLE, TRACKER, BUS_STOP, FLOOD,
          AREA, DISRUPTION, LOCAL_EVENT)

# Relationship types.
FROM, TO, USES, AFFECTS, ALONG, NEAR, DRIVES, CALLS_AT, COVERS, CROSSES = (
    "FROM", "TO", "USES", "AFFECTS", "ALONG", "NEAR", "DRIVES", "CALLS_AT", "COVERS", "CROSSES",
)  # fmt: skip
IN = "IN"
TYPES = (FROM, TO, USES, AFFECTS, ALONG, NEAR, DRIVES, CALLS_AT, COVERS, CROSSES, IN)


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


def bus_stops(rows: Iterable[dict[str, Any]], calls_at: Iterable[tuple[str, str]]) -> tuple[list[Node], list[Rel]]:
    """The stops a bus tracker watches, with positions from NaPTAN. Only these: the register holds
    400,000 stops, and the graph is for how things join up, not for a copy of the register."""
    nodes = [Node(BUS_STOP, r["id"], {"name": r["name"], "indicator": r.get("indicator"),
                                      "lat": r.get("lat"), "lon": r.get("lon")})
             for r in rows]  # fmt: skip
    rels = [Rel(CALLS_AT, (TRACKER, tracker), (BUS_STOP, stop)) for tracker, stop in calls_at]
    return nodes, rels


def floods(rows: Iterable[dict[str, Any]]) -> list[Node]:
    """Live flood warnings (alert, warning, severe). Their shapes stay in Postgres."""
    return [Node(FLOOD, r["id"], {"name": r.get("name"), "level": r.get("level"), "severity": r.get("severity"),
                                  "county": r.get("county"), "river_or_sea": r.get("river_or_sea")})
            for r in rows]  # fmt: skip


def flood_reach(covers: Iterable[tuple[str, str]], crosses: Iterable[tuple[str, str]]) -> list[Rel]:
    """COVERS: a warning's area contains a saved place. CROSSES: it crosses a watched corridor.
    Both are tested by PostGIS against the stored boundary."""
    return [*(Rel(COVERS, (FLOOD, w), (PLACE, p)) for w, p in covers),
            *(Rel(CROSSES, (FLOOD, w), (CORRIDOR, c)) for w, c in crosses)]  # fmt: skip


def disruptions(rows: Iterable[dict[str, Any]], affects: Iterable[tuple[str, str]]) -> tuple[list[Node], list[Rel]]:
    """Live TfL road disruptions (roadworks, collisions, closures) and the corridors they fall on."""
    nodes = [Node(DISRUPTION, r["id"], {"name": r.get("location") or r["id"], "category": r.get("category"),
                                        "severity": r.get("severity"), "scope": r.get("scope"),
                                        "status": r.get("status"), "closure": r.get("has_closures")})
             for r in rows]  # fmt: skip
    known = {n.id for n in nodes}
    return nodes, [Rel(AFFECTS, (DISRUPTION, d), (CORRIDOR, c)) for d, c in affects if d in known]


def local_events(rows: Iterable[dict[str, Any]]) -> list[Node]:
    """What's on in the next fortnight (Skiddle, Ticketmaster), held by the events sync."""
    return [Node(LOCAL_EVENT, r["id"], {"name": r["name"], "source": r.get("source"), "type": r.get("type"),
                                        "day": str(r["day"]) if r.get("day") else None,
                                        "venue": r.get("venue"), "town": r.get("town"),
                                        "lat": r.get("lat"), "lon": r.get("lon")})
            for r in rows]  # fmt: skip


def areas(
    rows: Iterable[dict[str, Any]], inside: Iterable[tuple[str, str, str]], parents: Iterable[tuple[str, str]]
) -> tuple[list[Node], list[Rel]]:
    """Administrative areas (Boundary-Line) and what sits in them.

    `inside` is (label, thing id, area id) and `parents` is (area id, wider area id), both worked out
    by PostGIS. Only areas something is in, and the wider areas above those, are kept: the graph is for
    how things join up, not a copy of every district in Britain.
    """
    by_id = {r["id"]: r for r in rows}
    up = dict(parents)
    keep: set[str] = set()
    for _, _, a in inside:
        while a in by_id and a not in keep:
            keep.add(a)
            a = up.get(a, "")
    nodes = [Node(AREA, a, {"name": by_id[a]["name"], "kind": by_id[a]["kind"], "code": by_id[a].get("code")})
             for a in sorted(keep)]  # fmt: skip
    rels = [Rel(IN, (label, thing), (AREA, a)) for label, thing, a in inside if a in keep]
    rels += [Rel(IN, (AREA, c), (AREA, p)) for c, p in parents if c in keep and p in keep]
    return nodes, rels


def near_places(pairs: Iterable[tuple[str, str, str, float]]) -> list[Rel]:
    """NEAR relationships from (label, thing id, place id, km) measured by PostGIS."""
    return [Rel(NEAR, (label, thing), (PLACE, place), {"km": round(float(km), 3)})
            for label, thing, place, km in pairs]  # fmt: skip


def along_corridors(pairs: Iterable[tuple[str, str, float]]) -> list[Rel]:
    """ALONG relationships from (station id, corridor id, km) measured by PostGIS."""
    return [Rel(ALONG, (STATION, station), (CORRIDOR, corridor), {"km": round(float(km), 3)})
            for station, corridor, km in pairs]  # fmt: skip


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
    bus_stop_rows: Sequence[dict[str, Any]] = (),
    calls_at: Sequence[tuple[str, str]] = (),
    flood_rows: Sequence[dict[str, Any]] = (),
    flood_covers: Sequence[tuple[str, str]] = (),
    flood_crosses: Sequence[tuple[str, str]] = (),
    disruption_rows: Sequence[dict[str, Any]] = (),
    disruption_corridors: Sequence[tuple[str, str]] = (),
    local_event_rows: Sequence[dict[str, Any]] = (),
    area_rows: Sequence[dict[str, Any]] = (),
    inside_pairs: Sequence[tuple[str, str, str]] = (),
    area_parents: Sequence[tuple[str, str]] = (),
    near_pairs: Sequence[tuple[str, str, str, float]] = (),
    along_pairs: Sequence[tuple[str, str, float]] = (),
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
    stop_nodes, stop_rels = bus_stops(bus_stop_rows, calls_at)
    flood_nodes = floods(flood_rows)
    disruption_nodes, disruption_rels = disruptions(disruption_rows, disruption_corridors)
    event_here = local_events(local_event_rows)
    area_nodes, area_rels = areas(area_rows, inside_pairs, area_parents)

    nodes = [*place_nodes, *trip_nodes, *corridor_nodes, *event_nodes, *station_nodes,
             *location_nodes, *vehicle_nodes, *tracker_nodes, *stop_nodes, *flood_nodes,
             *disruption_nodes, *event_here, *area_nodes]  # fmt: skip
    rels = [
        *trip_rels,
        *event_rels,
        *stop_rels,
        *disruption_rels,
        *area_rels,
        *flood_reach(flood_covers, flood_crosses),
        *near_places(near_pairs),
        *along_corridors(along_pairs),
        *drives(vehicle_nodes, trip_nodes),
    ]
    known = {n.key() for n in nodes}
    rels = [r for r in rels if r.start in known and r.end in known]  # never point at nothing
    return Graph(nodes, rels)
