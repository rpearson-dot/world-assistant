"""Walking the knowledge graph: what is connected to what, and what is disrupting a place.

Every answer here could be assembled from the other endpoints; the point of the graph is that it
follows the connections without being told the route in advance. When the graph is switched off or
cannot be reached, these say so plainly rather than pretending there is nothing to find.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import require_token
from app.graph import store
from app.graph.build import rebuild
from app.graph.model import LABELS, TYPES

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
