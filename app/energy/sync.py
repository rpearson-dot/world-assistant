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
from app.energy import kraken, octopus, store
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
    """Rates, then sessions. Each part stands alone: a failure in one is reported and does not stop the others,
    and the rates error (the one that matters most) is raised at the end so the job shows as failed."""
    owned = client is None
    client = client or httpx.AsyncClient(timeout=30.0, headers={"User-Agent": USER_AGENT})
    result: dict[str, Any] = {}
    rates_error: Exception | None = None
    s = get_settings()
    try:
        try:
            tariff = await resolve_tariff(client)
            rows = await octopus.fetch_rates(client, tariff)
            if not rows:
                raise octopus.OctopusError(
                    f"Octopus returned no rates for {tariff}; keeping what is stored"
                )
            result.update({"tariff": tariff, "rates": await store.save(engine, tariff, rows)})
        except (httpx.HTTPError, octopus.OctopusError, ValueError) as exc:
            rates_error = exc
        if s.octopus_account.strip() and s.octopus_api_key:
            try:
                saving = await kraken.fetch_saving(
                    client, s.octopus_api_key, s.octopus_account.strip()
                )
                result["saving_sessions"] = await store.save_sessions(
                    engine, ["saving", "boost"], saving
                )
            except (httpx.HTTPError, octopus.OctopusError, ValueError, KeyError, TypeError) as exc:
                result["saving_sessions_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        if s.energy_free_sessions_enabled:
            try:
                free = await kraken.fetch_free(client, s.energy_free_url)
                result["free_sessions"] = await store.save_sessions(engine, ["free"], free)
            except (httpx.HTTPError, octopus.OctopusError, ValueError, KeyError, TypeError) as exc:
                result["free_sessions_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    finally:
        if owned:
            await client.aclose()
    if rates_error is not None:
        raise rates_error
    return result


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
