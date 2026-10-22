"""Talking to Neo4j: writing the derived graph, and the few traversals the assistant needs.

Statement building is separated from running it, so the Cypher can be tested without a database.
Nothing here decides what the graph contains; app/graph/model.py does that.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from app.config import get_settings
from app.graph.model import LABELS, TYPES, Graph, Node, Rel

log = logging.getLogger(__name__)


class GraphUnavailable(RuntimeError):
    """The graph is switched off, or Neo4j could not be reached."""


# One uniqueness constraint per label: MERGE on id is then an index lookup, and a double build
# cannot produce two of anything.
CONSTRAINTS = [
    f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
    for label in LABELS
]


def node_statement(label: str) -> str:
    """MERGE every node of one label from a list of {id, props}."""
    if label not in LABELS:
        raise ValueError(f"unknown label {label!r}")
    return (
        "UNWIND $rows AS row "
        f"MERGE (n:{label} {{id: row.id}}) "
        "SET n += row.props, n.built = $built"
    )


def rel_statement(rel_type: str, start_label: str, end_label: str) -> str:
    """MERGE every relationship of one type between two labels."""
    if rel_type not in TYPES:
        raise ValueError(f"unknown relationship {rel_type!r}")
    if start_label not in LABELS or end_label not in LABELS:
        raise ValueError(f"unknown label in {start_label}-[{rel_type}]->{end_label}")
    return (
        "UNWIND $rows AS row "
        f"MATCH (a:{start_label} {{id: row.start}}) "
        f"MATCH (b:{end_label} {{id: row.end}}) "
        f"MERGE (a)-[r:{rel_type}]->(b) "
        "SET r += row.props, r.built = $built"
    )


def group_nodes(nodes: Sequence[Node]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for n in nodes:
        props = {k: v for k, v in n.props.items() if v is not None}
        out.setdefault(n.label, []).append({"id": n.id, "props": props})
    return out


def group_rels(rels: Sequence[Rel]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in rels:
        key = (r.type, r.start[0], r.end[0])
        out.setdefault(key, []).append({"start": r.start[1], "end": r.end[1], "props": r.props})
    return out


# Everything written by an older build is swept away, so a rebuild is a replacement rather than a
# pile-up. Relationships go first: Neo4j will not delete a node that still has one.
SWEEP_RELS = "MATCH ()-[r]->() WHERE r.built <> $built OR r.built IS NULL DELETE r"
SWEEP_NODES = "MATCH (n) WHERE n.built <> $built OR n.built IS NULL DETACH DELETE n"


_driver: Any = None
_lock = asyncio.Lock()


async def get_driver() -> Any:
    """The one driver this process uses. A driver holds a connection pool, so making a new one for
    every request would be wasteful; it is built on first use and closed at shutdown."""
    global _driver
    s = get_settings()
    if not s.graph_enabled:
        raise GraphUnavailable("the knowledge graph is switched off (WA_GRAPH_ENABLED)")
    async with _lock:
        if _driver is None:
            try:
                from neo4j import AsyncGraphDatabase
            except ImportError as exc:  # pragma: no cover - the driver is in the lock file
                raise GraphUnavailable("the neo4j driver is not installed") from exc
            _driver = AsyncGraphDatabase.driver(s.graph_url, auth=(s.graph_user, s.graph_password))
    return _driver


async def close() -> None:
    """Shut the driver down, at the end of the app's life."""
    global _driver
    async with _lock:
        if _driver is not None:
            await _driver.close()
            _driver = None


@asynccontextmanager
async def session() -> AsyncIterator[Any]:
    """A session on the shared driver, with anything the driver raises turned into GraphUnavailable."""
    d = await get_driver()
    try:
        async with d.session() as s:
            yield s
    except GraphUnavailable:
        raise
    except Exception as exc:  # the server is down, the password is wrong, the bolt port is shut
        raise GraphUnavailable(f"the knowledge graph could not be reached: {exc}") from exc


async def write(graph: Graph, built: str) -> dict[str, int]:
    """Replace the graph's contents with `graph`, stamping everything with this build's id."""
    async with session() as s:
        for statement in CONSTRAINTS:
            await s.run(statement)
        for label, rows in group_nodes(graph.nodes).items():
            await s.run(node_statement(label), rows=rows, built=built)
        for (rel_type, start, end), rows in group_rels(graph.rels).items():
            await s.run(rel_statement(rel_type, start, end), rows=rows, built=built)
        await s.run(SWEEP_RELS, built=built)
        await s.run(SWEEP_NODES, built=built)
    return graph.counts()


NEIGHBOURS = (
    "MATCH (n {id: $id}) WHERE $label IS NULL OR $label IN labels(n) "
    "MATCH (n)-[r]-(m) "
    "RETURN type(r) AS rel, startNode(r).id = n.id AS outgoing, "
    "labels(m)[0] AS label, m.id AS id, m.name AS name, properties(r) AS props "
    "ORDER BY rel, label, name LIMIT $limit"
)

# What matters about a place: the closures on the corridors of trips that start or end there.
DISRUPTION = (
    "MATCH (p:Place {id: $id})<-[:FROM|TO]-(t:Trip)-[:USES]->(c:Corridor)<-[:AFFECTS]-(e:RoadEvent) "
    "RETURN DISTINCT t.id AS trip, t.name AS trip_name, c.id AS corridor, c.name AS corridor_name, "
    "e.id AS event, e.kind AS kind, e.cause AS cause, e.limit_mph AS limit_mph "
    "ORDER BY trip_name, event LIMIT $limit"
)

# Warnings that cover a place itself, or cross the corridor of a trip that starts or ends there.
FLOOD_AT_PLACE = (
    "MATCH (p:Place {id: $id})<-[:COVERS]-(f:FloodWarning) "
    "RETURN f.id AS flood, f.name AS area, f.level AS level, f.severity AS severity, 'place' AS where_ "
    "ORDER BY level, area LIMIT $limit"
)
FLOOD_ON_ROUTE = (
    "MATCH (p:Place {id: $id})<-[:FROM|TO]-(t:Trip)-[:USES]->(c:Corridor)<-[:CROSSES]-(f:FloodWarning) "
    "RETURN DISTINCT f.id AS flood, f.name AS area, f.level AS level, f.severity AS severity, "
    "t.name AS trip_name, c.name AS corridor_name "
    "ORDER BY level, area LIMIT $limit"
)

COUNTS = "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS total ORDER BY label"
REL_COUNTS = "MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS total ORDER BY rel"


async def run(statement: str, **params: Any) -> list[dict[str, Any]]:
    async with session() as s:
        result = await s.run(statement, **params)
        return [dict(record) async for record in result]
