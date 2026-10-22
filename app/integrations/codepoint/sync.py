"""Code-Point Open: every UK postcode with a position, loaded from Ordnance Survey's open download.

Code-Point Open is updated quarterly and is about 25 MB zipped, so the job runs daily but downloads
only when the table is empty or a month old. The zip holds one headerless CSV per postcode area
(`Data/CSV/ab.csv`) with these columns, in this order:

    postcode, positional quality, eastings, northings, country, NHS region, NHS authority,
    county, district, ward

Postcodes the file marks as having no position (quality 90, or grid 0,0) are skipped. As with NaPTAN,
rows from an older load are deleted only when the file held enough to be the whole thing.

    python -m app.integrations.codepoint.sync [--file codepo_gb.zip]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import logging
import re
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
from app.jobs import tracked

log = logging.getLogger("wa.codepoint")

BATCH = 4000
MIN_POSTCODES = 1_000_000  # about 1.7 million exist
REFRESH_DAYS = 30
SHAPE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\d[A-Z]{2}$")

UPSERT = text(
    """
    INSERT INTO postcode (pc, postcode, easting, northing, quality, country, district, ward, seen)
    VALUES (:pc, :postcode, :easting, :northing, :quality, :country, :district, :ward, :seen)
    ON CONFLICT (pc) DO UPDATE SET postcode = EXCLUDED.postcode, easting = EXCLUDED.easting,
      northing = EXCLUDED.northing, quality = EXCLUDED.quality, country = EXCLUDED.country,
      district = EXCLUDED.district, ward = EXCLUDED.ward, seen = EXCLUDED.seen
    """
)


class IncompleteError(RuntimeError):
    """The download held too few postcodes to be the whole of Code-Point Open."""


def parse_row(row: list[str]) -> dict[str, Any] | None:
    """One CSV row as a postcode, or None when it has no usable position."""
    if len(row) < 4:
        return None
    pc = re.sub(r"\s+", "", row[0]).upper()
    if not SHAPE.match(pc):
        return None
    try:
        quality, easting, northing = int(row[1]), int(float(row[2])), int(float(row[3]))
    except ValueError:
        return None
    if quality >= 90 or (easting == 0 and northing == 0):
        return None
    cell = lambda i: (row[i].strip() or None) if len(row) > i else None
    return {"pc": pc, "postcode": f"{pc[:-3]} {pc[-3:]}", "easting": easting, "northing": northing,
            "quality": quality, "country": cell(4), "district": cell(8), "ward": cell(9)}  # fmt: skip


def read_rows(path: Path) -> Iterator[dict[str, Any]]:
    """Postcodes from the zip: every CSV under Data/CSV, however the areas are split."""
    with zipfile.ZipFile(path) as archive:
        names = sorted(n for n in archive.namelist() if n.lower().endswith(".csv") and "/data/" in f"/{n.lower()}")
        if not names:
            raise IncompleteError("no Data/CSV files in the zip")
        for name in names:
            with archive.open(name) as member:
                for row in csv.reader(io.TextIOWrapper(member, encoding="utf-8-sig", newline="")):
                    postcode = parse_row(row)
                    if postcode is not None:
                        yield postcode


async def load(engine: AsyncEngine, path: Path, started: datetime | None = None) -> dict[str, int]:
    started = started or datetime.now(UTC)
    seen: set[str] = set()
    batch: list[dict[str, Any]] = []

    async def flush() -> None:
        if batch:
            async with engine.begin() as conn:
                await conn.execute(UPSERT, batch)
            batch.clear()
            await asyncio.sleep(0)

    for p in read_rows(path):
        if p["pc"] in seen:
            continue
        seen.add(p["pc"])
        batch.append({**p, "seen": started})
        if len(batch) >= BATCH:
            await flush()
    await flush()

    if len(seen) < MIN_POSTCODES:
        raise IncompleteError(f"only {len(seen)} postcodes; kept what was there and removed nothing")
    async with engine.begin() as conn:
        removed = (await conn.execute(text("DELETE FROM postcode WHERE seen < :s"), {"s": started})).rowcount
    return {"postcodes": len(seen), "removed": removed}


async def due(engine: AsyncEngine) -> bool:
    """Empty, or last loaded more than a month ago."""
    async with engine.connect() as conn:
        newest = (await conn.execute(text("SELECT max(seen) FROM postcode"))).scalar()
    return newest is None or (datetime.now(UTC) - newest).days >= REFRESH_DAYS


async def download(url: str, dest: Path) -> int:
    size = 0
    async with (
        httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0), follow_redirects=True) as client,
        client.stream("GET", url) as response,
    ):
        response.raise_for_status()
        with dest.open("wb") as out:
            async for chunk in response.aiter_bytes(1 << 20):
                out.write(chunk)
                size += len(chunk)
    return size


async def sync(engine: AsyncEngine, url: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codepoint-") as tmp:
        path = Path(tmp) / "codepo.zip"
        size = await download(url, path)
        result = await load(engine, path)
    return {"megabytes": round(size / 1e6, 1), **result}


async def codepoint_sync(ctx: dict[str, Any]) -> Any:
    """arq job: daily, but only does anything when the data is missing or a month old."""
    settings = get_settings()

    async def run() -> Any:
        if not settings.codepoint_poll_enabled:
            return "disabled"
        if not await due(ctx["engine"]):
            return "up to date"
        return await sync(ctx["engine"], settings.codepoint_url)

    return await tracked(ctx["engine"], "codepoint", run)


async def _cli(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        print(await load(engine, Path(args.file)) if args.file else await sync(engine, settings.codepoint_url))
        return 0
    except (IncompleteError, httpx.HTTPError, zipfile.BadZipFile) as exc:
        print(f"Failed: {exc}")
        return 3
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load Code-Point Open postcodes into the database.")
    parser.add_argument("--file", help="load a codepo_gb.zip you already have rather than downloading")
    return asyncio.run(_cli(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
