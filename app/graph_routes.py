"""Walking the knowledge graph: what is connected to what, and what is disrupting a place.

Every answer here could be assembled from the other endpoints; the point of the graph is that it
follows the connections without being told the route in advance. When the graph is switched off or
cannot be reached, these say so plainly rather than pretending there is nothing to find.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.auth import require_token
from app.graph import explore, store
from app.graph.build import rebuild
from app.graph.model import LABELS, TYPES
from app.pagestyle import themed

router = APIRouter(prefix="/v1/graph", tags=["graph"], dependencies=[Depends(require_token)])


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(503, f"{exc}")


@router.get("/about")
async def about() -> dict[str, Any]:
    """What the graph is made of, and how much of it there is."""
    return {
        "labels": list(LABELS),
        "relationships": list(TYPES),
        "note": "Derived from Postgres; safe to drop and rebuild.",
    }


@router.get("/counts")
async def counts() -> dict[str, Any]:
    """How many of each node and relationship the graph currently holds."""
    try:
        nodes = await store.run(store.COUNTS)
        rels = await store.run(store.REL_COUNTS)
    except store.GraphUnavailable as exc:
        raise _unavailable(exc) from exc
    return {
        "nodes": {r["label"]: r["total"] for r in nodes},
        "relationships": {r["rel"]: r["total"] for r in rels},
    }


@router.get("/neighbours")
async def neighbours(
    id: str = Query(..., description="the id of the record, e.g. a place id"),
    label: str | None = Query(None, description="narrow to one kind of node"),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Everything joined to one thing, in either direction."""
    if label is not None and label not in LABELS:
        raise HTTPException(422, f"no label {label!r}; choose from {', '.join(LABELS)}")
    try:
        rows = await store.run(store.NEIGHBOURS, id=id, label=label, limit=limit)
    except store.GraphUnavailable as exc:
        raise _unavailable(exc) from exc
    return {"id": id, "neighbours": rows}


@router.get("/disruption")
async def disruption(
    place: str = Query(..., description="a place id"),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """What is currently disrupting the trips that start or end at a place.

    Follows place -> trip -> corridor -> road event in one hop-chain, so a closure three steps away
    from your home still surfaces. Flood warnings are checked the same way: over the place itself, and
    over the corridors of its trips.
    """
    try:
        rows = await store.run(store.DISRUPTION, id=place, limit=limit)
        at_place = await store.run(store.FLOOD_AT_PLACE, id=place, limit=limit)
        on_route = await store.run(store.FLOOD_ON_ROUTE, id=place, limit=limit)
    except store.GraphUnavailable as exc:
        raise _unavailable(exc) from exc
    trips = sorted({r["trip_name"] for r in rows})
    if not rows:
        summary = "Nothing on the roads of your trips from here."
    else:
        summary = (
            f"{len(rows)} thing(s) on the way on {len(trips)} trip(s): " + ", ".join(trips)
        )
    if at_place:
        summary += f" {len(at_place)} flood warning(s) cover this place."
    if on_route:
        summary += f" {len(on_route)} flood warning(s) cross the way."
    return {"place": place, "summary": summary, "disruptions": rows,
            "flood_warnings_here": at_place, "flood_warnings_on_route": on_route}  # fmt: skip


@router.post("/rebuild")
async def rebuild_now(request: Request) -> dict[str, Any]:
    """Rebuild the graph from Postgres now, rather than waiting for the scheduled run."""
    try:
        return await rebuild(request.app.state.engine)
    except store.GraphUnavailable as exc:
        raise _unavailable(exc) from exc


# --- exploring (the Graph page) -----------------------------------------------------------------------


class QueryIn(BaseModel):
    cypher: str = Field("", max_length=4000)
    preset: str | None = None


async def _view(cypher: str, **params: Any) -> dict[str, Any]:
    try:
        return await explore.read(cypher, **params)
    except store.GraphUnavailable as exc:
        raise _unavailable(exc) from exc
    except explore.QueryRefused as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/presets")
async def presets() -> dict[str, Any]:
    """Ready-made questions for the Graph page."""
    return {"presets": [{"id": k, "title": t, "cypher": c} for k, (t, c) in explore.PRESETS.items()],
            "labels": list(LABELS), "relationships": list(TYPES)}  # fmt: skip


@router.get("/overview")
async def overview(limit: int = Query(400, ge=1, le=500), per: int = Query(12, ge=1, le=100)) -> dict[str, Any]:
    """Your places, trips, vehicles and bus trackers, and what they connect to: up to `per` each, so every place
    shows, not only the busiest."""
    return await _view(explore.OVERVIEW, limit=limit, per=per)


@router.get("/search")
async def search(q: str = Query(..., min_length=1, max_length=80)) -> dict[str, Any]:
    """Nodes whose name or id contains the text."""
    return await _view(explore.SEARCH, q=q)


@router.get("/expand")
async def expand(id: str = Query(..., max_length=200), label: str | None = None,
                 limit: int = Query(80, ge=1, le=400)) -> dict[str, Any]:  # fmt: skip
    """One node and everything joined to it."""
    if label is not None and label not in LABELS:
        raise HTTPException(422, f"no label {label!r}")
    return await _view(explore.EXPAND, id=id, label=label, limit=limit)


@router.post("/query")
async def query(body: QueryIn) -> dict[str, Any]:
    """A preset, or your own read-only Cypher. Anything that would write or reach outside is refused."""
    if body.preset:
        if body.preset not in explore.PRESETS:
            raise HTTPException(404, "no such preset")
        cypher = explore.PRESETS[body.preset][1]
    else:
        try:
            cypher = explore.check(body.cypher)
        except explore.QueryRefused as exc:
            raise HTTPException(422, str(exc)) from exc
    return await _view(cypher)


page_router = APIRouter(include_in_schema=False)


@page_router.get("/graph", response_class=HTMLResponse)
async def graph_page(request: Request) -> str:
    from app.graph_page import PAGE

    return themed(PAGE, "graph", request)
