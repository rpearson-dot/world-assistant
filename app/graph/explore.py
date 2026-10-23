"""Exploring the knowledge graph from the Graph page: search, expand a node, presets, and your own Cypher.

Your own queries are read-only, three ways over: statements that write or reach outside (CREATE, MERGE, SET,
DELETE, LOAD CSV, CALL ...) are refused before they are sent; the session is opened in read mode, so Neo4j
itself refuses a write; and each runs with a time limit and a cap on what comes back. The graph is derived
from Postgres anyway, so nothing in it is lost for good.
"""

from __future__ import annotations

import re
from typing import Any

from app.graph import store

MAX_ROWS = 500
MAX_NODES = 400
TIMEOUT_S = 8.0

FORBIDDEN = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD|CALL|FOREACH|USE|ALTER|GRANT|DENY|REVOKE|START|STOP|"
    r"TERMINATE|SHOW|ENABLE|DISABLE|RENAME|FINISH|INSERT)\b",
    re.IGNORECASE,
)
STRINGS = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`[^`]*`")
COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


class QueryRefused(ValueError):
    """The query would write, reach outside the graph, or is not a query."""


def check(cypher: str) -> str:
    """The statement, if it only reads; else QueryRefused saying why."""
    text = (cypher or "").strip().rstrip(";").strip()
    if not text:
        raise QueryRefused("write a query, for example MATCH (n:Place) RETURN n")
    if len(text) > 4000:
        raise QueryRefused("that query is too long")
    if ";" in STRINGS.sub("''", text):
        raise QueryRefused("one statement at a time")
    bare = STRINGS.sub("''", COMMENTS.sub(" ", text))
    if m := FORBIDDEN.search(bare):
        raise QueryRefused(f"{m.group(1).upper()} is not allowed here: queries on this page only read")
    if not re.search(r"\bRETURN\b", bare, re.IGNORECASE):
        raise QueryRefused("a query must RETURN something")
    return text


PRESETS: dict[str, tuple[str, str]] = {
    "around-home": ("Everything around home",
                    ("MATCH (p:Place) WHERE p.kind = 'home' OR p.id = 'home' "
                    "MATCH path = (p)-[*1..2]-() RETURN path LIMIT 150")),
    "disruption": ("What is on my trips' roads",
                   ("MATCH path = (:Place)<-[:FROM|TO]-(:Trip)-[:USES]->(:Corridor)<-[:AFFECTS|CROSSES]-() "
                   "RETURN path LIMIT 150")),
    "fuel-along": ("Fuel stations along my routes",
                   "MATCH path = (:Trip)-[:USES]->(:Corridor)<-[:ALONG]-(:Station) RETURN path LIMIT 150"),
    "near-places": ("What is near my places",
                    "MATCH path = (:Place)<-[:NEAR]-() RETURN path LIMIT 200"),
    "floods": ("Flood warnings over places and routes",
               "MATCH path = (:FloodWarning)-[:COVERS|CROSSES]->() RETURN path LIMIT 150"),
    "buses": ("Bus trackers and their stops",
              "MATCH path = (:BusTracker)-[:CALLS_AT]->(:BusStop) RETURN path LIMIT 150"),
    "areas": ("What is in each area",
              "MATCH path = (a:Area)<-[:IN]-() RETURN path LIMIT 200"),
    "events": ("What's on near my places",
               "MATCH path = (:LocalEvent)-[:NEAR|IN]->() RETURN path LIMIT 200"),
    "roadworks": ("Road disruptions near places and routes",
                  "MATCH path = (:Disruption)-[:NEAR|AFFECTS]->() RETURN path LIMIT 200"),
    "trips": ("My trips, places and roads",
              "MATCH path = (:Place)<-[:FROM|TO]-(:Trip)-[:USES]->(:Corridor) RETURN path LIMIT 150"),
}  # fmt: skip

SEARCH = (
    "MATCH (n) WHERE toLower(toString(coalesce(n.name, ''))) CONTAINS toLower($q) "
    "OR toLower(toString(coalesce(n.id, ''))) CONTAINS toLower($q) "
    "RETURN n LIMIT 25"
)
EXPAND = (
    "MATCH (n) WHERE n.id = $id AND ($label IS NULL OR $label IN labels(n)) "
    "OPTIONAL MATCH (n)-[r]-(m) RETURN n, r, m LIMIT $limit"
)
OVERVIEW = "MATCH (n)-[r]-(m) WHERE n:Place OR n:Trip OR n:Vehicle OR n:BusTracker RETURN n, r, m LIMIT $limit"


def _plain(value: Any) -> Any:
    """Make a property JSON-friendly (Neo4j dates, points and so on become strings)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value][:50]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return str(value)


class View:
    """Collects the nodes and relationships found anywhere in a result, plus a table of the rest."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}
        self.truncated = False

    def node(self, n: Any) -> str:
        key = n.element_id
        if key not in self.nodes:
            if len(self.nodes) >= MAX_NODES:
                self.truncated = True
                return key
            props = {k: _plain(v) for k, v in dict(n).items() if k != "built"}
            label = next(iter(n.labels), "Node")
            self.nodes[key] = {"key": key, "label": label, "id": props.get("id"),
                               "name": props.get("name") or props.get("id") or label, "props": props}  # fmt: skip
        return key

    def rel(self, r: Any) -> None:
        if r.element_id in self.edges or r.start_node is None or r.end_node is None:
            return
        a, b = self.node(r.start_node), self.node(r.end_node)
        if a in self.nodes and b in self.nodes:
            props = {k: _plain(v) for k, v in dict(r).items() if k != "built"}
            self.edges[r.element_id] = {"key": r.element_id, "type": r.type, "from": a, "to": b, "props": props}

    def value(self, v: Any) -> Any:
        """Take in a value; return what the table shows for it."""
        from neo4j.graph import Node, Path, Relationship

        if isinstance(v, Node):
            self.node(v)
            return f"({next(iter(v.labels), '')} {dict(v).get('name') or dict(v).get('id')})"
        if isinstance(v, Relationship):
            self.rel(v)
            return f"[{v.type}]"
        if isinstance(v, Path):
            for n in v.nodes:
                self.node(n)
            for r in v.relationships:
                self.rel(r)
            return f"path of {len(v.relationships)}"
        if isinstance(v, (list, tuple)):
            return [self.value(x) for x in v][:50]
        return _plain(v)

    def result(self, columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
        return {"nodes": list(self.nodes.values()), "edges": list(self.edges.values()), "columns": columns,
                "rows": rows, "truncated": self.truncated}  # fmt: skip


async def read(cypher: str, **params: Any) -> dict[str, Any]:
    """Run a read-only statement and return it as graph (nodes, edges) and as a table (columns, rows)."""
    from neo4j import READ_ACCESS, unit_of_work
    from neo4j.exceptions import ClientError, DriverError, Neo4jError

    driver = await store.get_driver()
    view, rows, columns = View(), [], []

    @unit_of_work(timeout=TIMEOUT_S)  # a transaction function takes its time limit this way, not as a Query
    async def work(tx: Any) -> None:
        nonlocal columns
        result = await tx.run(cypher, **params)
        columns = list(result.keys())
        async for record in result:
            if len(rows) >= MAX_ROWS:
                view.truncated = True
                break
            rows.append([view.value(v) for v in record.values()])

    try:
        async with driver.session(default_access_mode=READ_ACCESS) as s:
            await s.execute_read(work)
    except ClientError as exc:  # a mistake in the query, a write refused, a timeout
        raise QueryRefused(exc.message or str(exc)) from exc
    except (Neo4jError, DriverError, OSError) as exc:  # the server is down, the password is wrong
        raise store.GraphUnavailable(f"the knowledge graph could not be reached: {exc}") from exc
    return view.result(columns, rows)
