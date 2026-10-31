"""GRIDSERVE: read every site, its chargers and their status, and the prices, on a schedule.

    every WA_GRIDSERVE_INTERVAL_MIN (60) minutes   sites, chargers and status (about 2 MB), then the prices

    python -m app.integrations.gridserve.sync

The whole network comes back in one answer, so each read replaces what is stored: a site or charger no longer listed
is dropped. An answer with far fewer sites than are stored is treated as a failed read and changes nothing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.integrations.gridserve import normalise as N
from app.integrations.gridserve.client import GridserveClient, GridserveError
from app.jobs import tracked

log = logging.getLogger("wa.gridserve")

SUSPICIOUS_DROP = 0.5  # an answer with fewer than half the stored sites is not trusted

SITE_UPSERT = text("""
INSERT INTO ev_site (id, operator, name, network, address, city, postcode, lat, lon, facilities, open_24h, opening,
                     updated_at, detail, seen)
VALUES (:id, :operator, :name, :network, :address, :city, :postcode, :lat, :lon, :facilities, :open_24h,
        CAST(:opening AS jsonb), :updated_at, CAST(:detail AS jsonb), :seen)
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, network = EXCLUDED.network, address = EXCLUDED.address,
  city = EXCLUDED.city, postcode = EXCLUDED.postcode, lat = EXCLUDED.lat, lon = EXCLUDED.lon,
  facilities = EXCLUDED.facilities, open_24h = EXCLUDED.open_24h, opening = EXCLUDED.opening,
  updated_at = EXCLUDED.updated_at, detail = EXCLUDED.detail, seen = EXCLUDED.seen
""")
EVSE_UPSERT = text("""
INSERT INTO ev_evse (id, site_id, status, connectors, max_kw, updated_at, detail, seen)
VALUES (:id, :site_id, :status, CAST(:connectors AS jsonb), :max_kw, :updated_at, CAST(:detail AS jsonb), :seen)
ON CONFLICT (id) DO UPDATE SET site_id = EXCLUDED.site_id, status = EXCLUDED.status, connectors = EXCLUDED.connectors,
  max_kw = EXCLUDED.max_kw, updated_at = EXCLUDED.updated_at, detail = EXCLUDED.detail, seen = EXCLUDED.seen
""")
TARIFF_UPSERT = text("""
INSERT INTO ev_tariff (id, currency, pence_per_kwh, detail, seen)
VALUES (:id, :currency, :pence_per_kwh, CAST(:detail AS jsonb), :seen)
ON CONFLICT (id) DO UPDATE SET currency = EXCLUDED.currency, pence_per_kwh = EXCLUDED.pence_per_kwh,
  detail = EXCLUDED.detail, seen = EXCLUDED.seen
""")


def _j(v: Any) -> str:
    return json.dumps(v, default=str)


async def sync(engine: AsyncEngine, client: GridserveClient, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    locations = await client.locations()
    sites, chargers = [], []
    for loc in locations:
        got = N.site(loc)
        if got:
            sites.append(got[0])
            chargers += got[1]
    async with engine.connect() as conn:
        stored = (await conn.execute(text("SELECT count(*) FROM ev_site WHERE operator = :o"), {"o": N.OPERATOR})).scalar_one()
    if stored and len(sites) < stored * SUSPICIOUS_DROP:
        raise GridserveError(f"GRIDSERVE listed {len(sites)} sites where {stored} are stored; kept what is stored")
    try:
        tariffs = [t for t in (N.tariff(x) for x in await client.tariffs()) if t]
    except GridserveError as exc:  # prices change rarely: keep the stored ones
        log.warning("GRIDSERVE prices not read: %s", exc)
        tariffs = []
    async with engine.begin() as conn:
        if sites:
            await conn.execute(SITE_UPSERT, [{**s, "opening": _j(s["opening"]), "detail": _j(s["detail"]), "seen": now}
                                             for s in sites])  # fmt: skip
        if chargers:
            await conn.execute(EVSE_UPSERT, [{**c, "connectors": _j(c["connectors"]), "detail": _j(c["detail"]), "seen": now}
                                             for c in chargers])  # fmt: skip
        gone_chargers = (await conn.execute(text(
            "DELETE FROM ev_evse e USING ev_site s WHERE e.site_id = s.id AND s.operator = :o AND e.seen < :now"),
            {"o": N.OPERATOR, "now": now})).rowcount  # fmt: skip
        gone_sites = (await conn.execute(text("DELETE FROM ev_site WHERE operator = :o AND seen < :now"),
                                         {"o": N.OPERATOR, "now": now})).rowcount  # fmt: skip
        if tariffs:
            await conn.execute(TARIFF_UPSERT, [{**t, "detail": _j(t["detail"]), "seen": now} for t in tariffs])
    statuses: dict[str, int] = {}
    for c in chargers:
        statuses[c["status"]] = statuses.get(c["status"], 0) + 1
    return {"sites": len(sites), "not_published": len(locations) - len(sites), "chargers": len(chargers),
            "status": dict(sorted(statuses.items())), "tariffs": len(tariffs),
            "removed": {"sites": gone_sites, "chargers": gone_chargers}}  # fmt: skip


def _client() -> GridserveClient:
    return GridserveClient(get_settings().gridserve_api_key)


async def gridserve_sync(ctx: dict[str, Any]) -> Any:
    """arq job."""

    async def go() -> Any:
        s = get_settings()
        if not s.gridserve_poll_enabled:
            return "disabled"
        if not s.gridserve_api_key:
            return "no key (gridserve_api_key)"
        async with _client() as c:
            return await sync(ctx["engine"], c)

    return await tracked(ctx["engine"], "gridserve", go)


async def _cli() -> int:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        async with _client() as c:
            print(await sync(engine, c))
        return 0
    except GridserveError as exc:
        print("failed:", exc)
        return 3
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli()))
