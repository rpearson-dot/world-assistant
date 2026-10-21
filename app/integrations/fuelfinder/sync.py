"""Fuel Finder sync: pull from the API (or a stored capture) into Postgres.

Stations change slowly (daily pull); prices change often (every 30 minutes). A
pull only replaces stored data when it looks complete, because the API sometimes
returns truncated results.

    python -m app.integrations.fuelfinder.sync --from-capture     # no API calls
    python -m app.integrations.fuelfinder.sync --pull             # stations then prices
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.integrations.fuelfinder.client import (
    PRICES,
    STATIONS,
    FuelFinderClient,
    FuelFinderError,
    IncompleteError,
    Limiter,
)
from app.integrations.fuelfinder.normalise import parse_prices, parse_station
from app.integrations.fuelfinder.store import apply_prices, save_stations
from app.jobs import tracked
from app.ratelimit import SlidingWindowLimiter

log = logging.getLogger("wa.fuel.sync")

MIN_STATIONS = 5000  # the UK has about 8,000; far fewer means a truncated pull
MIN_PRICE_COVERAGE = 0.9  # price records as a share of known stations


async def station_count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text("SELECT count(*) FROM fuel_station"))).scalar_one())


async def apply_stations(engine: AsyncEngine, records: list[dict[str, Any]]) -> dict[str, int]:
    stations = [s for r in records if (s := parse_station(r)) is not None]
    if len(stations) < MIN_STATIONS:
        raise IncompleteError(f"only {len(stations)} stations; refusing to treat as a full pull")
    saved = await save_stations(engine, stations, datetime.now(UTC))
    return {"stations": saved, "bad_coords": sum(not s.coords_ok for s in stations),
            "closed": sum(not s.open_for_business for s in stations)}  # fmt: skip


async def apply_price_records(engine: AsyncEngine, records: list[dict[str, Any]]) -> dict[str, int]:
    known = await station_count(engine)
    if known == 0:
        raise FuelFinderError("no stations stored yet; sync stations first")
    if len(records) < MIN_PRICE_COVERAGE * known:
        raise IncompleteError(
            f"{len(records)} price records for {known} stations; refusing to apply a short pull"
        )
    prices = [p for r in records for p in parse_prices(r)]
    return await apply_prices(engine, prices, datetime.now(UTC))


async def sync_stations(engine: AsyncEngine, client: FuelFinderClient) -> dict[str, int]:
    return await apply_stations(engine, await client.fetch_all(STATIONS))


async def sync_prices(engine: AsyncEngine, client: FuelFinderClient) -> dict[str, int]:
    return await apply_price_records(engine, await client.fetch_all(PRICES))


async def load_from_capture(engine: AsyncEngine, run_id: str | None = None) -> dict[str, Any]:
    """Load the most recent stored raw capture (no API calls)."""
    async with engine.connect() as conn:
        if run_id is None:
            run_id = (
                await conn.execute(
                    text(
                        "SELECT run_id FROM raw_capture WHERE call_name = 'ff_stations' "
                        "ORDER BY id DESC LIMIT 1"
                    )
                )
            ).scalar()
        if run_id is None:
            raise FuelFinderError("no stored Fuel Finder capture to load")
        pages: dict[str, list[dict[str, Any]]] = {"ff_stations": [], "ff_prices": []}
        rows = await conn.execute(
            text(
                "SELECT call_name, body FROM raw_capture WHERE run_id = :r "
                "AND call_name IN ('ff_stations', 'ff_prices') ORDER BY call_name, page_index"
            ),
            {"r": str(run_id)},
        )
        for name, body in rows:
            if isinstance(body, list):
                pages[name].extend(body)
    result: dict[str, Any] = {"run_id": str(run_id)}
    result["stations"] = await apply_stations(engine, pages["ff_stations"])
    result["prices"] = await apply_price_records(engine, pages["ff_prices"])
    return result


# ---- arq jobs --------------------------------------------------------------------------


async def _job(ctx: dict[str, Any], name: str, work: Any) -> Any:
    settings = get_settings()

    async def run() -> Any:
        if not settings.fuel_poll_enabled:
            return "disabled"
        cid, secret = settings.fuel_finder_client_id, settings.fuel_finder_client_secret
        if not cid or not secret:
            log.warning("fuel finder credentials are empty; skipping %s", name)
            return "no key"
        limiter: Limiter = ctx["fuel_limiter"]
        async with FuelFinderClient(cid, secret, limiter=limiter, limiter_max_wait=120.0) as client:
            return await work(ctx["engine"], client)

    return await tracked(ctx["engine"], name, run)


async def fuel_sync_stations(ctx: dict[str, Any]) -> Any:
    return await _job(ctx, "fuel_stations", sync_stations)


async def fuel_sync_prices(ctx: dict[str, Any]) -> Any:
    return await _job(ctx, "fuel_prices", sync_prices)


# ---- command line ------------------------------------------------------------------------


async def _cli(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    redis = Redis.from_url(settings.redis_dsn, decode_responses=True)
    try:
        if args.from_capture:
            print(await load_from_capture(engine, args.run))
            return 0
        cid, secret = settings.fuel_finder_client_id, settings.fuel_finder_client_secret
        if not cid or not secret:
            print("Fuel Finder credentials are empty.")
            return 2
        limiter = SlidingWindowLimiter(redis, "fuel", 60)
        async with FuelFinderClient(cid, secret, limiter=limiter) as client:
            print("stations:", await sync_stations(engine, client))
            print("prices:", await sync_prices(engine, client))
        return 0
    except FuelFinderError as exc:
        print(f"Failed: {exc}")
        return 3
    finally:
        await redis.aclose()
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync Fuel Finder into the database.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--from-capture", action="store_true", help="load the latest stored capture")
    group.add_argument("--pull", action="store_true", help="pull stations and prices from the API")
    parser.add_argument("--run", help="capture run id for --from-capture (default: latest)")
    return asyncio.run(_cli(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
