"""The Graph page's own Cypher: only reads get through."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.graph.explore import PRESETS, QueryRefused, check
from app.main import app


@pytest.mark.parametrize("cypher", [
    "MATCH (n:Place) RETURN n",
    "MATCH path = (p:Place)<-[:FROM|TO]-(t:Trip) RETURN path LIMIT 50;",
    "MATCH (n) WHERE n.name = 'Create a set' RETURN n",  # words inside strings are fine
    "// find places\nMATCH (n:Place) RETURN n.name AS name",
])  # fmt: skip
def test_reads_pass(cypher: str) -> None:
    assert check(cypher)


@pytest.mark.parametrize(("cypher", "why"), [
    ("MATCH (n) DETACH DELETE n", "DETACH"),
    ("CREATE (n:Place {id: 'x'}) RETURN n", "CREATE"),
    ("MATCH (n) SET n.name = 'x' RETURN n", "SET"),
    ("MERGE (n:Place {id: 'x'}) RETURN n", "MERGE"),
    ("LOAD CSV FROM 'http://evil' AS row RETURN row", "LOAD"),
    ("CALL dbms.components() YIELD name RETURN name", "CALL"),
    ("MATCH (n) RETURN n; MATCH (m) DELETE m", "one statement"),
    ("MATCH (n) RETURN n /* */ remove n.x", "REMOVE"),
    ("MATCH (n) WITH n", "RETURN"),
    ("", "write a query"),
])  # fmt: skip
def test_anything_else_is_refused(cypher: str, why: str) -> None:
    with pytest.raises(QueryRefused, match=why):
        check(cypher)


def test_the_presets_only_read() -> None:
    for title, cypher in PRESETS.values():
        assert title and check(cypher)


def test_the_api_refuses_before_reaching_neo4j(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    c, h = TestClient(app), {"Authorization": "Bearer tok"}
    r = c.post("/v1/graph/query", json={"cypher": "MATCH (n) DETACH DELETE n"}, headers=h)
    assert r.status_code == 422 and "not allowed" in r.json()["detail"]
    assert c.post("/v1/graph/query", json={"preset": "nope"}, headers=h).status_code == 404
    assert c.get("/v1/graph/presets", headers=h).json()["presets"]
    assert c.get("/v1/graph/overview", headers=h).status_code == 503  # the graph is off in tests: said so
