"""Manual capture of raw Fuel Finder responses, to learn the real JSON shape.

Run once on the Unraid box (the API only answers UK IP addresses):

    docker compose exec worker python -m app.integrations.fuelfinder.capture
    docker compose exec worker python -m app.integrations.fuelfinder.capture --export /tmp/ff
    docker compose cp worker:/tmp/ff ./samples/fuel

A full pull is about 35 requests and takes roughly 4 minutes. Pages are stored
verbatim in raw_capture (call names ff_stations and ff_prices).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.integrations.fuelfinder.client import (
    PRICES,
    STATIONS,
    AuthError,
    FuelFinderClient,
    FuelFinderError,
)
from app.integrations.nationalhighways.capture import export_run, latest_run_id, summarize
from app.ratelimit import SlidingWindowLimiter

CALLS = {"ff_stations": STATIONS, "ff_prices": PRICES}

_INSERT = text(
    """
    INSERT INTO raw_capture
      (run_id, call_name, page_index, request_path, request_params, status_code,
       body_bytes, body)
    VALUES
      (:run_id, :call_name, :page_index, :path, CAST(:params AS jsonb), 200,
       :bytes, CAST(:body AS jsonb))
    """
)


async def run_capture(engine: AsyncEngine, client: FuelFinderClient, run_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, path in CALLS.items():
        total = 0
        async for batch in client.batches(path):
            body = json.dumps(batch.raw).replace("\\u0000", "")
            async with engine.begin() as conn:
                await conn.execute(
                    _INSERT,
                    {"run_id": run_id, "call_name": name, "page_index": batch.index - 1,
                     "path": path, "params": json.dumps({"batch-number": batch.index}),
                     "bytes": len(body), "body": body},
                )  # fmt: skip
            total += len(batch.records)
            print(f"  {name} batch {batch.index}: {len(batch.records)} records ({total} total)")
        counts[name] = total
    return counts


async def _probe(path: str, redis: Redis) -> int:
    settings = get_settings()
    cid, secret = settings.fuel_finder_client_id, settings.fuel_finder_client_secret
    if not cid or not secret:
        print("Fuel Finder credentials are empty.")
        return 2
    limiter = SlidingWindowLimiter(redis, "fuel", 60)
    try:
        async with FuelFinderClient(cid, secret, limiter=limiter) as client:
            status, headers, body = await client.probe(path)
    except AuthError as exc:
        print(f"Login failed: {exc}. Wait 5 minutes before trying again.")
        return 3
    print(f"HTTP {status}")
    for key, value in headers.items():
        print(f"{key}: {value}")
    print("--- first 2000 characters of the body ---")
    print(body)
    return 0


async def _cli(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    redis = Redis.from_url(settings.redis_dsn, decode_responses=True)
    try:
        if args.probe:
            return await _probe(args.probe, redis)
        run_id = args.run
        if not args.no_fetch:
            cid, secret = settings.fuel_finder_client_id, settings.fuel_finder_client_secret
            if not cid or not secret:
                print("Fuel Finder credentials are empty: fill in the two secret files first.")
                return 2
            limiter = SlidingWindowLimiter(redis, "fuel", 60)
            run_id = str(uuid.uuid4())
            print("Pulling stations and prices (about 4 minutes)...")
            try:
                async with FuelFinderClient(cid, secret, limiter=limiter) as client:
                    counts = await run_capture(engine, client, run_id)
            except AuthError as exc:
                print(f"\nLogin failed: {exc}. Wait 5 minutes before trying again "
                      "(repeated failures extend the lockout).")  # fmt: skip
                return 3
            except FuelFinderError as exc:
                print(f"\nPull failed part-way: {exc}. Partial pages are stored.")
                return 4
            print(f"\nDone: {counts}")
        run_id = run_id or await latest_run_id(engine)
        if not run_id:
            print("No captures stored yet.")
            return 1
        for row in await summarize(engine, run_id):
            print(f"  {row['call_name']:<12} pages={row['pages']:<3} bytes={row['bytes']}")
        if args.export:
            count = await export_run(engine, run_id, Path(args.export))
            print(f"Exported {count} files to {args.export}")
        return 0
    finally:
        await redis.aclose()
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture raw Fuel Finder responses.")
    parser.add_argument("--probe", metavar="PATH", help="print one raw response, e.g. /pfs, to diagnose")
    parser.add_argument("--no-fetch", action="store_true", help="skip fetching; summarise/export a stored run")
    parser.add_argument("--run", help="run id to summarise/export with --no-fetch (default: latest)")
    parser.add_argument("--export", metavar="DIR", help="write the run's pages to files in DIR")
    return asyncio.run(_cli(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
