"""Fetch the tariff's unit rates and store them. Runs every 30 minutes; tomorrow's rates appear mid-afternoon.

python -m app.energy.sync
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.energy import octopus, store
from app.jobs import tracked

USER_AGENT = "world-assistant (self-hosted)"


async def resolve_tariff(client: httpx.AsyncClient) -> str:
    s = get_settings()
    if s.octopus_tariff.strip():
        return s.octopus_tariff.strip().upper()
    if not s.octopus_account.strip():
        raise octopus.OctopusError(
            "set WA_OCTOPUS_ACCOUNT (and the octopus_api_key secret), or WA_OCTOPUS_TARIFF"
        )
    key = s.octopus_api_key
    if not key:
        raise octopus.OctopusError("the octopus_api_key secret is empty")
    return await octopus.discover_tariff(client, s.octopus_account.strip(), key)


async def sync(engine: AsyncEngine, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    owned = client is None
    client = client or httpx.AsyncClient(timeout=30.0, headers={"User-Agent": USER_AGENT})
    try:
        tariff = await resolve_tariff(client)
        rows = await octopus.fetch_rates(client, tariff)
        if not rows:
            raise octopus.OctopusError(
                f"Octopus returned no rates for {tariff}; keeping what is stored"
            )
        return {"tariff": tariff, "rates": await store.save(engine, tariff, rows)}
    finally:
        if owned:
            await client.aclose()


async def energy_sync(ctx: dict[str, Any]) -> Any:
    """arq job."""

    async def run() -> Any:
        if not get_settings().energy_poll_enabled:
            return "disabled"
        s = get_settings()
        if not (s.octopus_tariff.strip() or s.octopus_account.strip()):
            return "not configured"
        return await sync(ctx["engine"])

    return await tracked(ctx["engine"], "energy", run)


async def _cli() -> int:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        print(await sync(engine))
        return 0
    except (httpx.HTTPError, octopus.OctopusError, ValueError) as exc:
        print(f"Failed: {exc}")
        return 3
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli()))
