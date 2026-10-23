"""Boundary-Line: where the regions, counties, districts and boroughs of Britain are, from Ordnance Survey.

Boundary-Line is published twice a year (May and October) under the Open Government Licence. The download is
a GeoPackage of one layer per kind of area. By default this keeps the three that say which council or region
a place is in (`region`, `county`, `district`); `WA_BOUNDARY_KINDS` adds `ward`, `parish` and `constituency`.

Each area is simplified (WA_BOUNDARY_SIMPLIFY_M, default 20 m) while it is loaded, so it is a few hundred
points rather than tens of thousands. That is plenty to say which district a point is in, and it keeps the
table a few tens of MB for the default kinds. As with the other national loads, old rows are deleted only
when the file held enough to be the whole thing.

    python -m app.integrations.boundaryline.sync [--file bdline_gb.zip] [--list]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
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
from app.jobs import tracked

log = logging.getLogger("wa.boundary")

BATCH = 300
REFRESH_DAYS = 150  # published twice a year
KINDS = ("region", "county", "district", "ward", "parish", "constituency")
MIN_AREAS = {"region": 9, "county": 20, "district": 300}  # Great Britain has about 380 districts, 9 English regions
SUFFIX = re.compile(
    r"\s*(\((?:b|b\)|unitary|county)\)?|london boro|london borough|district|boro|borough|county|unitary authority|"
    r"co const|burgh const|boro const|p const|const|ed|cp|community|parish|city|metropolitan)\s*$", re.IGNORECASE)  # fmt: skip

UPSERT = text(
    """
    INSERT INTO boundary (id, kind, name, code, seen, geom)
    VALUES (:id, :kind, :name, :code, :seen,
            ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Transform(
              ST_SimplifyPreserveTopology(ST_SetSRID(ST_GeomFromWKB(:wkb), :srs), :tol), 4326)), 3)))
    ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, name = EXCLUDED.name, code = EXCLUDED.code,
      geom = CASE WHEN boundary.seen = EXCLUDED.seen
                  THEN ST_Multi(ST_CollectionExtract(ST_Union(boundary.geom, EXCLUDED.geom), 3))
                  ELSE EXCLUDED.geom END,
      seen = EXCLUDED.seen
    """
)


class IncompleteError(RuntimeError):
    """The download held too few areas to be the whole of Boundary-Line."""


def kind_of(table: str) -> str | None:
    """Which kind of area a GeoPackage layer holds, from its name; None for layers not used."""
    t = table.lower()
    if "ward" in t and "district" in t:
        return "ward"
    if "community_ward" in t or "electoral_division" in t or "historic" in t or "ceremonial" in t:
        return None
    if "english_region" in t or t in ("region", "scotland_and_wales_region"):
        return "region" if "english" in t or t == "region" else None
    if t == "county" or t.endswith("_county") and "ceremonial" not in t:
        return "county"
    if t.startswith("district_borough_unitary") or t == "district":
        return "district"
    if "parish" in t or t.endswith("community"):
        return "parish"
    if "const" in t:
        return "constituency"
    return None


def clean_name(name: str) -> str:
    """'Kingston upon Thames London Boro' -> 'Kingston upon Thames'. Names without a suffix are unchanged."""
    out = (name or "").strip()
    for _ in range(3):
        shorter = SUFFIX.sub("", out).strip()
        if shorter == out or not shorter:
            break
        out = shorter
    return out


def area_id(kind: str, code: str | None, name: str) -> str:
    return f"{kind}:{code or re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}"


def wanted_kinds(setting: str) -> set[str]:
    return {k for k in (s.strip().lower() for s in setting.split(",")) if k in KINDS}


def describe(path: Path) -> list[dict[str, Any]]:
    """What is in a GeoPackage: each layer, the kind it would load as, and its columns (for `--list`)."""
    db = osgpkg.open_db(path)
    try:
        return [{"table": layer.table, "kind": kind_of(layer.table), "columns": list(layer.columns)}
                for layer in osgpkg.layers(db)]  # fmt: skip
    finally:
        db.close()


async def load(engine: AsyncEngine, path: Path, kinds: set[str], tolerance_m: float,
               started: datetime | None = None) -> dict[str, Any]:  # fmt: skip
    started = started or datetime.now(UTC)
    db = osgpkg.open_db(path)
    counts: dict[str, int] = {}
    try:
        for layer in osgpkg.layers(db):
            kind = kind_of(layer.table)
            if kind not in kinds:
                continue
            name_col, code_col = osgpkg.pick(layer.columns, "name", "names"), osgpkg.pick(layer.columns, "code", "gss_code", "gss_cd")
            if name_col is None:
                log.warning("boundary layer %s has no name column; skipped", layer.table)
                continue
            batch: list[dict[str, Any]] = []
            seen_ids: set[str] = set()

            async def flush(batch: list[dict[str, Any]] = batch) -> None:
                if batch:
                    async with engine.begin() as conn:
                        await conn.execute(UPSERT, batch)
                    batch.clear()
                    await asyncio.sleep(0)

            for attrs, wkb, srs in osgpkg.features(db, layer, tuple(c for c in (name_col, code_col) if c)):
                name = clean_name(str(attrs.get(name_col) or ""))
                if not name:
                    continue
                code = str(attrs[code_col]).strip() if code_col and attrs.get(code_col) else None
                aid = area_id(kind, code, name)
                seen_ids.add(aid)
                batch.append({"id": aid, "kind": kind, "name": name, "code": code, "seen": started,
                              "wkb": wkb, "srs": srs, "tol": tolerance_m})  # fmt: skip
                if len(batch) >= BATCH:
                    await flush()
            await flush()
            counts[kind] = counts.get(kind, 0) + len(seen_ids)
    finally:
        db.close()
    short = {k: n for k, n in counts.items() if n < MIN_AREAS.get(k, 0)}
    if short or not counts:
        raise IncompleteError(f"too few areas ({counts or 'none'}); kept what was there and removed nothing")
    async with engine.begin() as conn:
        removed = (await conn.execute(
            text("DELETE FROM boundary WHERE seen < :s AND kind = ANY(:k)"), {"s": started, "k": sorted(counts)})).rowcount
    return {"areas": counts, "removed": removed}


async def due(engine: AsyncEngine, kinds: set[str]) -> bool:
    """A wanted kind is missing, or the oldest of them was loaded more than ~5 months ago."""
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT kind, max(seen) FROM boundary GROUP BY kind"))).all()
    have = {k: s for k, s in rows}
    if not kinds <= set(have):
        return True
    return any((datetime.now(UTC) - have[k]).days >= REFRESH_DAYS for k in kinds)


async def download(url: str, dest: Path) -> int:
    size = 0
    async with (
        httpx.AsyncClient(timeout=httpx.Timeout(900.0, connect=30.0), follow_redirects=True) as client,
        client.stream("GET", url) as response,
    ):
        response.raise_for_status()
        with dest.open("wb") as out:
            async for chunk in response.aiter_bytes(1 << 20):
                out.write(chunk)
                size += len(chunk)
    return size


async def sync(engine: AsyncEngine, url: str, kinds: set[str], tolerance_m: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="boundary-") as tmp:
        path = Path(tmp) / "boundary-line.zip"
        size = await download(url, path)
        gpkg = osgpkg.unpack(path, Path(tmp))
        path.unlink()  # one copy on disk at a time
        result = await load(engine, gpkg, kinds, tolerance_m)
    return {"megabytes": round(size / 1e6, 1), **result}


async def boundary_sync(ctx: dict[str, Any]) -> Any:
    """arq job: daily, but only does anything when a wanted kind is missing or the data is months old."""
    settings = get_settings()
    kinds = wanted_kinds(settings.boundary_kinds)

    async def run() -> Any:
        if not settings.boundary_poll_enabled or not kinds:
            return "disabled"
        if not await due(ctx["engine"], kinds):
            return "up to date"
        return await sync(ctx["engine"], settings.boundary_url, kinds, settings.boundary_simplify_m)

    return await tracked(ctx["engine"], "boundary", run)


async def _cli(args: argparse.Namespace) -> int:
    settings = get_settings()
    kinds = wanted_kinds(args.kinds or settings.boundary_kinds)
    try:
        if args.list:
            with tempfile.TemporaryDirectory(prefix="boundary-") as tmp:
                path = Path(args.file) if args.file else Path(tmp) / "boundary-line.zip"
                if not args.file:
                    print("Downloading the Boundary-Line file to look inside it (large; use --file to reuse one)...")
                    await download(settings.boundary_url, path)
                gpkg = osgpkg.unpack(path, Path(tmp)) if zipfile.is_zipfile(path) else path
                for row in describe(gpkg):
                    print(row)
            return 0
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        try:
            if args.file:
                with tempfile.TemporaryDirectory(prefix="boundary-") as tmp:
                    gpkg = osgpkg.unpack(Path(args.file), Path(tmp)) if args.file.lower().endswith(".zip") else Path(args.file)
                    print(await load(engine, gpkg, kinds, settings.boundary_simplify_m))
            else:
                print(await sync(engine, settings.boundary_url, kinds, settings.boundary_simplify_m))
        finally:
            await engine.dispose()
        return 0
    except (IncompleteError, osgpkg.GpkgError, httpx.HTTPError, zipfile.BadZipFile) as exc:
        print(f"Failed: {exc}")
        return 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load Ordnance Survey Boundary-Line areas into the database.")
    parser.add_argument("--file", help="a Boundary-Line zip or .gpkg you already have, instead of downloading")
    parser.add_argument("--list", action="store_true", help="show the layers in --file and the kind each would load as")
    parser.add_argument("--kinds", help="comma-separated: region,county,district,ward,parish,constituency")
    return asyncio.run(_cli(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
