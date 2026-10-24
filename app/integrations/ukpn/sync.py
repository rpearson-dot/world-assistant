"""UK Power Networks: read the portal on a schedule and keep what is about your places.

    power cuts          hourly; every 15 minutes while a cut affects home. Every cut seen is also kept for good
                        (ukpn_fault_history), with the places it was at
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

# Rota Load Disconnection is published as a document, not map data, so it cannot be looked up for a place.
AREA_KINDS = ("boundary", "idno", "gsp", "primary", "secondary")
SUSPICIOUS_EMPTY_OVER = 20
AT_PLACE_M = 1000  # a cut this close to a saved place, or naming its postcode, is at that place (as app/power.py)

# Is cut f at place p? Its postcodes name the place's postcode, or (when it names only postcode sectors, as UK Power
# Networks does) the place's sector; or it is within :m metres. Used here and in app/power.py.
AT = """(
  (p.postcode IS NOT NULL AND (
     upper(trim(p.postcode)) = ANY(f.postcodes)
     OR (regexp_replace(upper(trim(p.postcode)), '^([A-Z]{1,2}[0-9][A-Z0-9]?)\\s*([0-9])[A-Z]{2}$', '\\1 \\2') = ANY(f.postcodes)
         AND NOT EXISTS (SELECT 1 FROM unnest(f.postcodes) x WHERE x ~ '[0-9][A-Z]{2}$'))))
  OR coalesce(ST_DWithin(f.geog, p.geog, :m), false))"""
# Which saved places each cut in the feed is at.
AT_PLACES = f"""
SELECT f.id, array_agg(p.id ORDER BY p.id) AS places, bool_or(p.kind = 'home' OR p.id = 'home') AS home
FROM ukpn_feed f JOIN place p ON {AT}
GROUP BY f.id
"""
# Keep every cut: insert the new, update the ongoing, and close the ones no longer in the feed.
HISTORY_UPSERT = """
INSERT INTO ukpn_fault_history (id, planned, description, postcodes, customers_max, started_at, first_restore_at,
  last_restore_at, status_last, first_seen, last_seen, ended_at, places, home, lat, lon)
SELECT f.id, f.planned, f.description, f.postcodes, f.customers, f.started_at, f.restore_at, f.restore_at, f.status,
       f.seen, f.seen, CASE WHEN f.active THEN NULL ELSE coalesce(f.ended_at, f.seen) END,
       coalesce(a.places, '{}'), coalesce(a.home, false), f.lat, f.lon
FROM ukpn_feed f LEFT JOIN at_places a ON a.id = f.id
ON CONFLICT (id) DO UPDATE SET
  planned = EXCLUDED.planned, description = coalesce(EXCLUDED.description, ukpn_fault_history.description),
  postcodes = (SELECT array_agg(DISTINCT x) FROM unnest(ukpn_fault_history.postcodes || EXCLUDED.postcodes) x),
  customers_max = GREATEST(ukpn_fault_history.customers_max, EXCLUDED.customers_max),
  started_at = coalesce(ukpn_fault_history.started_at, EXCLUDED.started_at),
  first_restore_at = coalesce(ukpn_fault_history.first_restore_at, EXCLUDED.last_restore_at),
  last_restore_at = coalesce(EXCLUDED.last_restore_at, ukpn_fault_history.last_restore_at),
  status_last = coalesce(EXCLUDED.status_last, ukpn_fault_history.status_last),
  last_seen = EXCLUDED.last_seen,
  ended_at = CASE WHEN EXCLUDED.ended_at IS NULL THEN NULL
                  ELSE coalesce(ukpn_fault_history.ended_at, EXCLUDED.ended_at) END,
  places = (SELECT coalesce(array_agg(DISTINCT x), '{}') FROM unnest(ukpn_fault_history.places || EXCLUDED.places) x),
  home = ukpn_fault_history.home OR EXCLUDED.home, lat = coalesce(EXCLUDED.lat, ukpn_fault_history.lat),
  lon = coalesce(EXCLUDED.lon, ukpn_fault_history.lon)
"""


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
    feed = list({r["id"]: r for r in (N.fault(x, now) for x in await c.export(ds))}.values())
    rows = [r for r in feed if r["active"]]  # the feed also lists cuts already over ("Complete")
    async with engine.begin() as conn:
        held = (await conn.execute(text("SELECT count(*) FROM ukpn_fault"))).scalar_one()
        if not feed and held > SUSPICIOUS_EMPTY_OVER:
            raise UkpnError("the portal returned no power cuts; keeping the stored ones")
        # the whole feed, finished cuts included, so a short cut between two reads still reaches the history
        await conn.execute(text("CREATE TEMP TABLE ukpn_feed (LIKE ukpn_fault INCLUDING GENERATED) ON COMMIT DROP"))
        await conn.execute(text("ALTER TABLE ukpn_feed ADD COLUMN active boolean, ADD COLUMN ended_at timestamptz"))
        if feed:
            await conn.execute(text(
                "INSERT INTO ukpn_feed (id, planned, status, description, postcodes, customers, started_at, restore_at, "
                "lat, lon, detail, seen, active, ended_at) VALUES (:id, :planned, :status, :description, :postcodes, "
                ":customers, :started_at, :restore_at, :lat, :lon, CAST(:detail AS jsonb), :seen, :active, :ended_at)"),
                _json(feed))  # fmt: skip
        await conn.execute(text("DELETE FROM ukpn_fault"))
        await conn.execute(text(
            "INSERT INTO ukpn_fault (id, planned, status, description, postcodes, customers, started_at, restore_at, lat, "
            "lon, detail, seen) SELECT id, planned, status, description, postcodes, customers, started_at, restore_at, "
            "lat, lon, detail, seen FROM ukpn_feed WHERE active"))  # fmt: skip
        await conn.execute(text(f"WITH at_places AS ({AT_PLACES}) {HISTORY_UPSERT}"), {"m": AT_PLACE_M})
        ended = (await conn.execute(text(
            "UPDATE ukpn_fault_history SET ended_at = :now WHERE ended_at IS NULL "
            "AND id NOT IN (SELECT id FROM ukpn_feed)"), {"now": now})).rowcount  # fmt: skip
        ended += (await conn.execute(text(
            "SELECT count(*) FROM ukpn_fault_history h JOIN ukpn_feed f ON f.id = h.id "
            "WHERE NOT f.active AND h.last_seen = :now"), {"now": now})).scalar_one()  # fmt: skip
        home = await home_affected(conn)
        await conn.execute(text(  # when the feed was last read, even if it had no cuts in it (see faults_due)
            "INSERT INTO app_setting (key, value) VALUES ('ukpn_faults_read', CAST(:v AS jsonb)) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
            {"v": json.dumps({"at": now.isoformat(), "home": home})})  # fmt: skip
    return {"power_cuts": len(rows), "finished_in_feed": len(feed) - len(rows),
            "planned": sum(r["planned"] is True for r in rows), "placed": sum(r["lat"] is not None for r in rows),
            "ended": ended, "at_home": home}  # fmt: skip


async def home_affected(conn: Any) -> bool:
    """Is a current cut (planned or not) at home: naming its postcode, or within 1 km?"""
    return bool((await conn.execute(text(
        f"SELECT EXISTS (SELECT 1 FROM ukpn_fault f JOIN place p ON (p.kind = 'home' OR p.id = 'home') AND {AT})"),
        {"m": AT_PLACE_M})).scalar())  # fmt: skip


async def faults_due(engine: AsyncEngine, now: datetime | None = None) -> tuple[bool, str]:
    """Read power cuts now? Hourly normally; every 15 minutes while a cut is at home (the job runs every 15)."""
    s = get_settings()
    now = now or datetime.now(UTC)
    async with engine.connect() as conn:
        read = (await conn.execute(text("SELECT value FROM app_setting WHERE key = 'ukpn_faults_read'"))).scalar()
        home = await home_affected(conn)
    last = datetime.fromisoformat(read["at"]) if isinstance(read, dict) and read.get("at") else None
    every = s.ukpn_fault_alert_interval_min if home else s.ukpn_fault_interval_min
    if last is None or now - last >= timedelta(minutes=every - 2):  # a little slack for the job's own timing
        return True, f"every {every} minutes" + (" (a cut is at home)" if home else "")
    return False, f"next read due {every} minutes after the last" + (" (a cut is at home)" if home else "")


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
    """Half-hourly carbon intensity at your places' grid supply points, yesterday onwards. A reading belongs to a
    place's grid supply point when its functional location or its name matches ('BEDDINGTON' and 'BEDDINGTON (SPN)')."""
    now = now or datetime.now(UTC)
    async with engine.connect() as conn:
        gsps = sorted({(r[0], r[1]) for r in await conn.execute(text(
            "SELECT name, code FROM ukpn_place_area WHERE kind = 'gsp'")) if r[0]})  # fmt: skip
    if not gsps:
        return {"readings": 0, "grid_supply_points": 0}
    ds = await c.dataset("carbon")
    dates = ds.of_type("datetime", "date")
    where = f"{dates[0]} >= date'{(now - timedelta(days=1)).date().isoformat()}'" if dates else None
    rows: dict[tuple[str, datetime], float] = {}
    for rec in await c.export(ds, where=where):
        got = N.carbon(rec)
        if not got:
            continue
        name, at, value, floc = got
        mine = next((g for g, code in gsps if (floc and floc == code) or N.same_gsp(name, g)), None)
        if mine:
            rows[(mine, at)] = value
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


async def _faults_when_due(engine: AsyncEngine, c: UkpnClient) -> Any:
    due, why = await faults_due(engine)
    if not due:
        log.debug("power cuts not read: %s", why)
        return "up to date"  # recorded as skipped on the status page
    return {**(await sync_faults(engine, c)), "pace": why}


ukpn_faults = _job("ukpn_faults", _faults_when_due)
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
