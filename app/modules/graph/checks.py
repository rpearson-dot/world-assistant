"""The graph's line on the Setup page ("Your setup"), plugged in through the registry ("setup.check")."""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.setup_checklist import FAILING, OFF, READY, WAITING


async def graph(s: Settings) -> dict[str, Any]:
    out: dict[str, Any] = {"id": "graph", "name": "Knowledge graph (Neo4j)", "link": "/graph",
                           "unlocks": "The Graph page: how your places, trips, roads and stations connect"}  # fmt: skip
    if not s.graph_enabled:
        out["state"], out["note"] = OFF, "Optional container: docker-compose.graph.yml and WA_GRAPH_ENABLED=true."
        return out
    from app.modules.graph import store

    try:
        rows = await store.run(store.COUNTS)
    except store.GraphUnavailable as exc:
        out["state"], out["error"] = FAILING, str(exc)[:300]
        return out
    total = sum(int(r["total"]) for r in rows)
    out["state"] = READY if total else WAITING
    out["note"] = f"{total} nodes" if total else "Built twice an hour; or Rebuild on the Graph page."
    return out
