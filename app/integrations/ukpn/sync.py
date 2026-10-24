"""UK Power Networks: read the portal on a schedule and keep what is about your places.

    power cuts          every 15 minutes   all of them (a few hundred at most)
    street works now    every 2 hours      all of them, so a trip's whole route can be checked
    proposed works      daily              all of them
    your places         daily              which licence area, independent operator, grid supply point, primary and
                                           secondary substation and rota block each saved place is in
    carbon intensity    every 30 minutes   at the grid supply points your places are fed from

    python -m app.integrations.ukpn.sync [faults|works|proposed|places|carbon|all]

Nothing is read unless a saved place is inside UK Power Networks' area (worked out by `places`), so the module
costs nothing elsewhere. A list that comes back empty over a stored one is treated as a failed read.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.integrations.ukpn import normalise as N
from app.integrations.ukpn.client import UkpnClient, UkpnError, compact
from app.jobs import tracked

log = logging.getLogger("wa.ukpn")

AREA_KINDS = ("boundary", "idno", "gsp", "primary", "secondary", "rota")
SUSPICIOUS_EMPTY_OVER = 20


async def covered(engine: AsyncEngine) -> bool:
    """Is any saved place on UK Power Networks' network (inside its boundary, not an independent operator's area)?"""
    async with engine.connect() as conn:
        n = (await conn.execute(text(
            "SELECT count(*) FROM place p WHERE EXISTS (SELECT 1 FROM ukpn_place_area a WHERE a.place_id = p.id "
            "AND a.kind = 'boundary') AND NOT EXISTS (SELECT 1 FROM ukpn_place_area a WHERE a.place_id = p.id "
            "AND a.kind = 'idno')"))).scalar_one()  # fmt: skip
    return bool(n)


def _json(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**r, "detail": json.dumps(compact(r["detail"]))} for r in rows]


async def sync_faults(engine: AsyncEngine, c: UkpnClient, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    ds = await c.dataset("faults")
    rows = list({r["id"]: r for r in (N.fault(x, now) for x in await c.export(ds))}.values())
    async with engine.begin() as conn:
        held = (await conn.execute(text("SELECT count(*) FROM ukpn_fault"))).scalar_one()
        if not rows and held > SUSPICIOUS_EMPTY_OVER:
            raise UkpnError("the portal returned no power cuts; keeping the stored ones")
        await conn.execute(text("DELETE FROM ukpn_fault"))
        if rows:
            await conn.execute(text(
                "INSERT INTO ukpn_fault (id, planned, status, description, postcodes, customers, started_at, restore_at, "
                "lat, lon, detail, seen) VALUES (:id, :planned, :status, :description, :postcodes, :customers, "
                ":started_at, :restore_at, :lat, :lon, CAST(:detail AS jsonb), :seen)"), _json(rows))  # fmt: skip
    return {"power_cuts": len(rows), "planned": sum(r["planned"] is True for r in rows),
            "placed": sum(r["lat"] is not None for r in rows)}  # fmt: skip


async def sync_works(engine: AsyncEngine, c: UkpnClient, stage: str, now: datetime | None = None) -> dict[str, int]:
    """stage: open (works under way) or proposed (permit applications)."""
    now = now or datetime.now(UTC)
    ds = await c.dataset("works_open" if stage == "open" else "works_proposed")
    rows = list({r["id"]: r for r in (N.works(x, stage, now) for x in await c.export(ds))}.values())
    async with engine.begin() as conn:
        held = (await conn.execute(text("SELECT count(*) FROM ukpn_works WHERE stage = :s"), {"s": stage})).scalar_one()
        if not rows and held > SUSPICIOUS_EMPTY_OVER:
            raise UkpnError(f"the portal returned no {stage} works; keeping the stored ones")
        await conn.execute(text("DELETE FROM ukpn_works WHERE stage = :s"), {"s": stage})
        if rows:
            await conn.execute(text(
                "INSERT INTO ukpn_works (id, stage, street, town, usrn, category, traffic, description, starts_at, "
                "ends_at, lat, lon, detail, seen) VALUES (:id, :stage, :street, :town, :usrn, :category, :traffic, "
                ":description, :starts_at, :ends_at, :lat, :lon, CAST(:detail AS jsonb), :seen) "
                "ON CONFLICT (id) DO NOTHING"), _json(rows))  # fmt: skip
    return {f"{stage}_works": len(rows), "placed": sum(r["lat"] is not None for r in rows)}


async def sync_places(engine: AsyncEngine, c: UkpnClient, now: datetime | None = None) -> dict[str, Any]:
    """For every saved place, the parts of the network it sits in. A dataset the portal does not have is skipped."""
    now = now or datetime.now(UTC)
    async with engine.connect() as conn:
        places = [(r[0], float(r[1]), float(r[2])) for r in await conn.execute(text("SELECT id, lat, lon FROM place"))]
    found: list[dict[str, Any]] = []
    missing: list[str] = []
    for kind in AREA_KINDS:
        try:
            ds = await c.dataset(kind)
        except UkpnError as exc:
            missing.append(f"{kind}: {exc}")
            continue
        for pid, lat, lon in places:
            try:
                recs = await c.at_point(ds, lat, lon)
            except UkpnError as exc:
                missing.append(f"{kind} at {pid}: {exc}")
                break
            for rec in recs[:3]:
                a = N.area(rec, kind)
                found.append({**a, "place_id": pid, "detail": json.dumps(compact(rec)), "seen": now})
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM ukpn_place_area WHERE kind = ANY(:k)"),
                           {"k": [k for k in AREA_KINDS if not any(m.startswith(k + ":") for m in missing)]})  # fmt: skip
        if found:
            await conn.execute(text(
                "INSERT INTO ukpn_place_area (place_id, kind, code, name, detail, seen) VALUES (:place_id, :kind, :code, "
                ":name, CAST(:detail AS jsonb), :seen) ON CONFLICT (place_id, kind, code) DO UPDATE SET name = "
                "EXCLUDED.name, detail = EXCLUDED.detail, seen = EXCLUDED.seen"), found)  # fmt: skip
    out: dict[str, Any] = {"places": len(places), "areas": len(found)}
    if missing:
        out["skipped"] = missing[:6]
    return out


async def sync_carbon(engine: AsyncEngine, c: UkpnClient, now: datetime | None = None) -> dict[str, int]:
    """Half-hourly carbon intensity at your places' grid supply points, yesterday onwards."""
    now = now or datetime.now(UTC)
    async with engine.connect() as conn:
        gsps = sorted({r[0] for r in await conn.execute(text("SELECT name FROM ukpn_place_area WHERE kind = 'gsp'")) if r[0]})
    if not gsps:
        return {"readings": 0, "grid_supply_points": 0}
    ds = await c.dataset("carbon")
    dates = ds.of_type("datetime", "date")
    where = f"{dates[0]} >= date'{(now - timedelta(days=1)).date().isoformat()}'" if dates else None
    rows: dict[tuple[str, datetime], float] = {}
    for rec in await c.export(ds, where=where):
        got = N.carbon(rec)
        if got and any(N.same_gsp(got[0], g) for g in gsps):
            gsp = next(g for g in gsps if N.same_gsp(got[0], g))
            rows[(gsp, got[1])] = got[2]
    if rows:
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO ukpn_carbon (gsp, at, intensity) VALUES (:g, :t, :v) ON CONFLICT (gsp, at) DO UPDATE SET "
                "intensity = EXCLUDED.intensity"), [{"g": g, "t": t, "v": v} for (g, t), v in rows.items()])  # fmt: skip
            await conn.execute(text("DELETE FROM ukpn_carbon WHERE at < :old"), {"old": now - timedelta(days=7)})
    return {"readings": len(rows), "grid_supply_points": len(gsps)}


def _client() -> UkpnClient:
    return UkpnClient(get_settings().ukpn_api_key)


def _job(name: str, run: Any, needs_cover: bool = True) -> Any:
    async def job(ctx: dict[str, Any]) -> Any:
        async def go() -> Any:
            if not get_settings().ukpn_poll_enabled:
                return "disabled"
            if needs_cover and not await covered(ctx["engine"]):
                return "no saved place is on UK Power Networks' network"
            async with _client() as c:
                return await run(ctx["engine"], c)

        return await tracked(ctx["engine"], name, go)

    job.__name__ = f"ukpn_{name.removeprefix('ukpn_')}"
    return job


ukpn_faults = _job("ukpn_faults", sync_faults)
ukpn_works_open = _job("ukpn_works", lambda e, c: sync_works(e, c, "open"))
ukpn_works_proposed = _job("ukpn_proposed", lambda e, c: sync_works(e, c, "proposed"))
ukpn_places = _job("ukpn_places", sync_places, needs_cover=False)
ukpn_carbon = _job("ukpn_carbon", sync_carbon)


async def _cli(what: str) -> int:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    steps = {"places": sync_places, "faults": sync_faults, "works": lambda e, c: sync_works(e, c, "open"),
             "proposed": lambda e, c: sync_works(e, c, "proposed"), "carbon": sync_carbon}  # fmt: skip
    try:
        async with _client() as c:
            for name, step in steps.items():
                if what in ("all", name):
                    try:
                        print(name, await step(engine, c))
                    except UkpnError as exc:
                        print(name, "failed:", exc)
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli(sys.argv[1] if len(sys.argv) > 1 else "all")))
