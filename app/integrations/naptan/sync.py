"""NaPTAN sync: download the national register and keep it in Postgres.

The register changes slowly, so this runs weekly. The download is about 100 MB of CSV, streamed to a
temporary file rather than held in memory, and written in batches. Rows are stamped with the time
of the sync; rows from an earlier sync are deleted at the end, and only when the file looked
complete, so a truncated download can only ever add or update, never wipe.

    python -m app.integrations.naptan.sync            # download and load
    python -m app.integrations.naptan.sync --file x.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import logging
import sys
import tempfile
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.integrations.naptan.normalise import parse_row
from app.jobs import tracked

log = logging.getLogger("wa.naptan")

BATCH = 5000
MIN_STOPS = 200_000  # Great Britain has about 400,000; far fewer means a truncated file

UPSERT = text(
    """
    INSERT INTO naptan_stop (atco, naptan_code, name, street, indicator, locality, stop_type, kind,
                             lat, lon, seen)
    VALUES (:atco, :naptan_code, :name, :street, :indicator, :locality, :stop_type, :kind,
            :lat, :lon, :seen)
    ON CONFLICT (atco) DO UPDATE SET
      naptan_code = EXCLUDED.naptan_code, name = EXCLUDED.name, street = EXCLUDED.street,
      indicator = EXCLUDED.indicator, locality = EXCLUDED.locality, stop_type = EXCLUDED.stop_type,
      kind = EXCLUDED.kind, lat = EXCLUDED.lat, lon = EXCLUDED.lon, seen = EXCLUDED.seen
    """
)


class IncompleteError(RuntimeError):
    """The file held too few stops to be the whole register."""


def read_rows(path: Path) -> Iterator[dict[str, Any]]:
    """Stops from a NaPTAN CSV, or from a zip with one inside it."""
    with path.open("rb") as raw:
        zipped = raw.read(4) == b"PK\x03\x04"
    if zipped:
        with zipfile.ZipFile(path) as archive:
            name = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
            with archive.open(name) as member:
                yield from _parse(io.TextIOWrapper(member, encoding="utf-8-sig", newline=""))
    else:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            yield from _parse(handle)


def _parse(handle: Any) -> Iterator[dict[str, Any]]:
    for row in csv.DictReader(handle):
        stop = parse_row(row)
        if stop is not None:
            yield stop


async def load(engine: AsyncEngine, path: Path, started: datetime | None = None) -> dict[str, int]:
    """Upsert every stop in the file, then drop the ones the file no longer has."""
    started = started or datetime.now(UTC)
    seen: set[str] = set()
    batch: list[dict[str, Any]] = []

    async def flush() -> None:
        if batch:
            async with engine.begin() as conn:
                await conn.execute(UPSERT, batch)
            batch.clear()
            await asyncio.sleep(0)  # let the worker breathe between batches

    for stop in read_rows(path):
        if stop["atco"] in seen:
            continue  # a stop listed twice: the first wins, and one statement cannot hit a key twice
        seen.add(stop["atco"])
        batch.append({**stop, "seen": started})
        if len(batch) >= BATCH:
            await flush()
    await flush()

    if len(seen) < MIN_STOPS:
        raise IncompleteError(f"only {len(seen)} stops; kept what was there and removed nothing")
    async with engine.begin() as conn:
        removed = (await conn.execute(text("DELETE FROM naptan_stop WHERE seen < :s"), {"s": started})).rowcount
    return {"stops": len(seen), "removed": removed}


async def download(url: str, dest: Path) -> int:
    """Stream the register to disk. Returns the size in bytes."""
    size = 0
    timeout = httpx.Timeout(900.0, connect=30.0)
    async with (
        httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client,
        client.stream("GET", url) as response,
    ):
        response.raise_for_status()
        with dest.open("wb") as out:
            async for chunk in response.aiter_bytes(1 << 20):
                out.write(chunk)
                size += len(chunk)
    return size


async def sync(engine: AsyncEngine, url: str) -> dict[str, int]:
    with tempfile.TemporaryDirectory(prefix="naptan-") as tmp:
        path = Path(tmp) / "naptan.csv"
        size = await download(url, path)
        result = await load(engine, path)
    return {"megabytes": round(size / 1e6, 1), **result}


async def naptan_sync(ctx: dict[str, Any]) -> Any:
    """arq job."""
    settings = get_settings()

    async def run() -> Any:
        if not settings.naptan_poll_enabled:
            return "disabled"
        return await sync(ctx["engine"], settings.naptan_url)

    return await tracked(ctx["engine"], "naptan", run)


async def _cli(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        if args.file:
            print(await load(engine, Path(args.file)))
        else:
            print(await sync(engine, settings.naptan_url))
        return 0
    except (IncompleteError, httpx.HTTPError) as exc:
        print(f"Failed: {exc}")
        return 3
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load the NaPTAN stop register into the database.")
    parser.add_argument("--file", help="load a CSV you already have rather than downloading")
    return asyncio.run(_cli(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
