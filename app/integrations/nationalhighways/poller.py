"""Scheduled polling: fetch National Highways feeds, store, and tag by corridor.

Two jobs share the one rate-limit budget:

* poll_live: unplanned closures, unplanned speed limits and VMS signs (frequent)
* poll_planned: planned closures and speed limits for the coming days (hourly)

A feed is only used to mark events as cleared when every page of it was
fetched, so a truncated or failed fetch never wipes good data.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings, get_settings
from app.integrations.nationalhighways.client import (
    CLOSURES,
    SPEED_MANAGED_AREAS,
    VMS,
    Limiter,
    NationalHighwaysClient,
    iso,
)
from app.integrations.nationalhighways.match import Corridor
from app.integrations.nationalhighways.normalise import parse_events, parse_vms
from app.integrations.nationalhighways.store import (
    clear_unseen,
    load_corridors,
    save_events,
    save_signs,
)
from app.jobs import tracked
from app.ratelimit import RateLimitExceeded

log = logging.getLogger("wa.nh.poller")


@dataclass(frozen=True)
class Fetch:
    payloads: list[dict[str, Any]]
    complete: bool  # every page fetched without error


async def fetch_all(
    client: NationalHighwaysClient, path: str, params: dict[str, str], max_pages: int
) -> Fetch:
    payloads: list[dict[str, Any]] = []
    complete = False
    try:
        async for page in client.pages(path, params, max_pages=max_pages):
            if not page.ok or not isinstance(page.json, dict):
                log.warning("%s page %d failed: status=%s %s", path, page.index, page.status_code, page.error)
                return Fetch(payloads, False)
            payloads.append(page.json.get("D2Payload", page.json))
            complete = page.x_next is None
    except RateLimitExceeded:
        log.warning("local rate limit budget exhausted while fetching %s", path)
        return Fetch(payloads, False)
    return Fetch(payloads, complete)


def vms_bbox(settings: Settings, corridors: list[Corridor]) -> str:
    """Bounding box covering every enabled corridor, else the configured default."""
    if not corridors:
        return settings.nh_vms_bbox
    boxes = [c.bbox(margin_m=500) for c in corridors]
    lat0 = min(b[0] for b in boxes)
    lon0 = min(b[1] for b in boxes)
    lat1 = max(b[2] for b in boxes)
    lon1 = max(b[3] for b in boxes)
    return f"{lat0:.4f},{lon0:.4f},{lat1:.4f},{lon1:.4f}"


async def _poll_events(
    engine: AsyncEngine,
    client: NationalHighwaysClient,
    corridors: list[Corridor],
    *,
    scope: str,
    kind: str,
    path: str,
    params: dict[str, str],
    max_pages: int,
) -> dict[str, int | bool]:
    seen = datetime.now(UTC)
    fetched = await fetch_all(client, path, params, max_pages)
    events = parse_events(fetched.payloads)
    on_corridor = await save_events(engine, events, corridors, scope, seen)
    cleared = await clear_unseen(engine, scope, kind, seen) if fetched.complete else 0
    log.info(
        "%s %s: %d records, %d on corridors, %d cleared, complete=%s",
        scope, kind, len(events), on_corridor, cleared, fetched.complete,
    )  # fmt: skip
    return {"records": len(events), "on_corridor": on_corridor, "cleared": cleared,
            "complete": fetched.complete}  # fmt: skip


async def poll_live_with(
    engine: AsyncEngine, client: NationalHighwaysClient, settings: Settings
) -> dict[str, Any]:
    now = datetime.now(UTC)
    corridors = await load_corridors(engine)
    window = {"startDateTime": iso(now - timedelta(hours=24)), "endDateTime": iso(now)}
    result: dict[str, Any] = {}
    result["closures"] = await _poll_events(
        engine, client, corridors, scope="unplanned", kind="closure", path=CLOSURES,
        params={"closureType": "unplanned", **window}, max_pages=10,
    )  # fmt: skip
    result["speed"] = await _poll_events(
        engine, client, corridors, scope="unplanned", kind="speed", path=SPEED_MANAGED_AREAS,
        params={"speedRestrictionType": "unplanned", **window}, max_pages=10,
    )  # fmt: skip
    vms = await fetch_all(client, VMS, {"bBox": vms_bbox(settings, corridors)}, 10)
    signs = parse_vms(vms.payloads)
    changed = await save_signs(engine, signs, now)
    log.info("vms: %d signs, %d message changes, complete=%s", len(signs), changed, vms.complete)
    result["vms"] = {"signs": len(signs), "changed": changed, "complete": vms.complete}
    return result


async def poll_planned_with(
    engine: AsyncEngine, client: NationalHighwaysClient, settings: Settings
) -> dict[str, Any]:
    now = datetime.now(UTC)
    corridors = await load_corridors(engine)
    window = {"startDateTime": iso(now), "endDateTime": iso(now + timedelta(days=settings.nh_planned_days))}
    result: dict[str, Any] = {}
    result["closures"] = await _poll_events(
        engine, client, corridors, scope="planned", kind="closure", path=CLOSURES,
        params={"closureType": "planned", **window}, max_pages=settings.nh_planned_max_pages,
    )  # fmt: skip
    result["speed"] = await _poll_events(
        engine, client, corridors, scope="planned", kind="speed", path=SPEED_MANAGED_AREAS,
        params={"speedRestrictionType": "planned", **window}, max_pages=settings.nh_planned_max_pages,
    )  # fmt: skip
    return result


async def _run(
    ctx: dict[str, Any], name: str, job: Callable[..., Any], enabled: bool, max_wait: float = 30.0
) -> dict[str, Any] | str:
    settings = get_settings()

    async def work() -> dict[str, Any] | str:
        if not enabled:
            return "disabled"
        key = settings.nh_primary_key
        if not key:
            log.warning("nh_subscription_key is empty; skipping poll")
            return "no key"
        limiter: Limiter = ctx["limiter"]
        async with NationalHighwaysClient(
            key, limiter, settings.nh_base_url, limiter_max_wait=max_wait
        ) as client:
            return await job(ctx["engine"], client, settings)  # type: ignore[no-any-return]

    return await tracked(ctx["engine"], name, work)  # type: ignore[no-any-return]


async def poll_live(ctx: dict[str, Any]) -> dict[str, Any] | str:
    """arq job: live feeds. Needs ctx['engine'] and ctx['limiter']."""
    return await _run(ctx, "nh_poll_live", poll_live_with, get_settings().nh_poll_enabled)


async def poll_planned(ctx: dict[str, Any]) -> dict[str, Any] | str:
    """arq job: planned works. Needs ctx['engine'] and ctx['limiter']."""
    # A planned pull is many pages; let it wait out the per-minute budget rather than stop early.
    return await _run(
        ctx, "nh_poll_planned", poll_planned_with, get_settings().nh_poll_enabled, max_wait=150.0
    )
