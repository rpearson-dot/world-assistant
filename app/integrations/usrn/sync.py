"""Open USRN: the street reference number and line of every street in Great Britain, from Ordnance Survey.

A USRN is the number roadworks are reported against: Street Manager attaches one to every job. This holds each
street's number and its line so a job can be placed on the street it is on. It is large (a million and more
streets), so it is **off unless WA_USRN_POLL_ENABLED=true**, and only reloaded when it is missing or a year old
(OS publishes it monthly, but street numbers hardly ever change).

    python -m app.integrations.usrn.sync [--file open_usrn.zip] [--list]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.integrations import osgpkg
from app.integrations.boundaryline.sync import download
from app.jobs import tracked

log = logging.getLogger("wa.usrn")

BATCH = 2000
MIN_STREETS = 1_000_000  # Great Britain has about 1.7 million
REFRESH_DAYS = 365
SIMPLIFY_M = 1.0

UPSERT = text(
    """
    INSERT INTO usrn (usrn, seen, geom)
    VALUES (:usrn, :seen, ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Transform(
              ST_SimplifyPreserveTopology(ST_SetSRID(ST_GeomFromWKB(:wkb), :srs), :tol), 4326)), 2)))
    ON CONFLICT (usrn) DO UPDATE SET geom = EXCLUDED.geom, seen = EXCLUDED.seen
    """
)


class IncompleteError(RuntimeError):
    """The download held too few streets to be the whole of Open USRN."""


def describe(path: Path) -> list[dict[str, Any]]:
    db = osgpkg.open_db(path)
    try:
        return [{"table": layer.table, "columns": list(layer.columns)} for layer in osgpkg.layers(db)]
    finally:
        db.close()


async def load(engine: AsyncEngine, path: Path, started: datetime | None = None) -> dict[str, int]:
    started = started or datetime.now(UTC)
    db = osgpkg.open_db(path)
    seen = 0
    try:
        layer = next((x for x in osgpkg.layers(db) if osgpkg.pick(x.columns, "usrn")), None)
        if layer is None:
            raise osgpkg.GpkgError("no layer with a usrn column in the GeoPackage")
        col = osgpkg.pick(layer.columns, "usrn") or "usrn"
        batch: list[dict[str, Any]] = []

        async def flush() -> None:
            if batch:
                async with engine.begin() as conn:
                    await conn.execute(UPSERT, batch)
                batch.clear()
                await asyncio.sleep(0)

        for attrs, wkb, srs in osgpkg.features(db, layer, (col,)):
            try:
                number = int(attrs[col])
            except (TypeError, ValueError):
                continue
            batch.append({"usrn": number, "seen": started, "wkb": wkb, "srs": srs, "tol": SIMPLIFY_M})
            seen += 1
            if len(batch) >= BATCH:
                await flush()
        await flush()
    finally:
        db.close()
    if seen < MIN_STREETS:
        raise IncompleteError(f"only {seen} streets; kept what was there and removed nothing")
    async with engine.begin() as conn:
        removed = (await conn.execute(text("DELETE FROM usrn WHERE seen < :s"), {"s": started})).rowcount
    return {"streets": seen, "removed": removed}


async def due(engine: AsyncEngine) -> bool:
    async with engine.connect() as conn:
        newest = (await conn.execute(text("SELECT max(seen) FROM usrn"))).scalar()
    return newest is None or (datetime.now(UTC) - newest).days >= REFRESH_DAYS


async def sync(engine: AsyncEngine, url: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="usrn-") as tmp:
        path = Path(tmp) / "open-usrn.zip"
        size = await download(url, path)
        gpkg = osgpkg.unpack(path, Path(tmp))
        path.unlink()
        result = await load(engine, gpkg)
    return {"megabytes": round(size / 1e6, 1), **result}


async def usrn_sync(ctx: dict[str, Any]) -> Any:
    """arq job: daily, but only does anything when switched on and the data is missing or a year old."""
    settings = get_settings()

    async def run() -> Any:
        if not settings.usrn_poll_enabled:
            return "disabled"
        if not await due(ctx["engine"]):
            return "up to date"
        return await sync(ctx["engine"], settings.usrn_url)

    return await tracked(ctx["engine"], "usrn", run)


async def _cli(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        if args.list:
            path = Path(args.file)
            if path.suffix.lower() == ".zip":
                with tempfile.TemporaryDirectory(prefix="usrn-") as tmp:
                    print(describe(osgpkg.unpack(path, Path(tmp))))
            else:
                print(describe(path))
            return 0
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        try:
            if args.file:
                with tempfile.TemporaryDirectory(prefix="usrn-") as tmp:
                    gpkg = osgpkg.unpack(Path(args.file), Path(tmp)) if args.file.lower().endswith(".zip") else Path(args.file)
                    print(await load(engine, gpkg))
            else:
                print(await sync(engine, settings.usrn_url))
        finally:
            await engine.dispose()
        return 0
    except (IncompleteError, osgpkg.GpkgError, httpx.HTTPError, zipfile.BadZipFile) as exc:
        print(f"Failed: {exc}")
        return 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load Ordnance Survey Open USRN streets into the database.")
    parser.add_argument("--file", help="a zip or .gpkg you already have, instead of downloading")
    parser.add_argument("--list", action="store_true", help="show the layers in --file")
    return asyncio.run(_cli(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
