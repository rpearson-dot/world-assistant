"""What's on near your places, copied from Skiddle and Ticketmaster so it can be held, related and drawn.

The events page and API ask the providers live. This keeps the next `WA_EVENTS_SYNC_DAYS` days (14) around each of
your saved places (home first, at most `WA_EVENTS_SYNC_PLACES`, 20; up to `WA_EVENTS_SYNC_PAGES` pages of 100 from
each source at each), every six hours, so the knowledge graph can
relate an event to its place and district. It needs the same keys as the events feature and does nothing without
one. Events that are over, or that a provider no longer lists, are dropped.

    python -m app.integrations.localevents.sync
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.integrations.skiddle.client import DEFAULT_BASE, SkiddleClient, SkiddleError
from app.integrations.ticketmaster.client import DEFAULT_BASE as TM_BASE
from app.integrations.ticketmaster.client import TicketmasterClient, TicketmasterError
from app.jobs import tracked
from app.ratelimit import SlidingWindowLimiter

log = logging.getLogger("wa.events")

LIMIT = 100  # per page, place and source
SKIDDLE_GAP_S = 2.1
UPSERT = text(
    """
    INSERT INTO local_event (id, source, name, day, starts, ends, type, venue, venue_id, town, postcode, lat, lon,
                             price, url, seen)
    VALUES (:id, :source, :name, :day, :starts, :ends, :type, :venue, :venue_id, :town, :postcode, :lat, :lon, :price,
            :url, :seen)
    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, day = EXCLUDED.day, starts = EXCLUDED.starts,
      ends = EXCLUDED.ends, type = EXCLUDED.type, venue = EXCLUDED.venue, venue_id = EXCLUDED.venue_id,
      town = EXCLUDED.town, postcode = EXCLUDED.postcode, lat = EXCLUDED.lat, lon = EXCLUDED.lon,
      price = EXCLUDED.price, url = EXCLUDED.url, seen = EXCLUDED.seen
    """
)
# The venues events are at, kept (for the graph) after their events have gone; one not seen for VENUE_DAYS is dropped.
VENUES = text(
    """
    INSERT INTO event_venue (id, name, town, postcode, lat, lon, first_seen, last_seen)
    VALUES (:id, :name, :town, :postcode, :lat, :lon, :seen, :seen)
    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, town = COALESCE(EXCLUDED.town, event_venue.town),
      postcode = COALESCE(EXCLUDED.postcode, event_venue.postcode), lat = EXCLUDED.lat, lon = EXCLUDED.lon,
      last_seen = EXCLUDED.last_seen
    """
)
VENUE_DAYS = 365


def venue_id(name: Any, postcode: Any, lat: float, lon: float) -> str | None:
    """One venue however each source spells it: its name in plain letters, with its postcode (or, without one, where it
    is to about 100 m). None without a name."""
    plain = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    if not plain:
        return None
    pc = re.sub(r"\s+", "", str(postcode or "")).upper()
    return f"{plain}:{pc}" if re.fullmatch(r"[A-Z]{1,2}\d[A-Z\d]?\d[A-Z]{2}", pc) else f"{plain}:{lat:.3f},{lon:.3f}"


def _ends(v: Any) -> datetime | None:
    """A provider's end time, when it gives one with a time of day (a bare date says nothing about when it ends)."""
    s = str(v or "")
    if len(s) <= 10:
        return None
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=ZoneInfo(get_settings().local_tz))


def to_row(source: str, e: dict[str, Any], seen: datetime) -> dict[str, Any] | None:
    """A provider's flat event dict as a stored row; None when it has no id, name, date or position."""
    try:
        day = date.fromisoformat(str(e.get("date") or "")[:10])
    except ValueError:
        return None
    if not e.get("id") or not e.get("name") or e.get("lat") is None or e.get("lon") is None:
        return None
    lat, lon = float(e["lat"]), float(e["lon"])
    return {"id": f"{source}:{e['id']}", "source": source, "name": e["name"], "day": day, "starts": e.get("doors_open"),
            "ends": _ends(e.get("end_date")), "type": e.get("type"), "venue": e.get("venue"),
            "venue_id": venue_id(e.get("venue"), e.get("postcode"), lat, lon), "town": e.get("town"),
            "postcode": e.get("postcode"), "lat": lat, "lon": lon, "price": e.get("price"), "url": e.get("url"),
            "seen": seen}  # fmt: skip


async def places(engine: AsyncEngine, limit: int) -> list[tuple[str, float, float]]:
    async with engine.connect() as conn:
        rows = (await conn.execute(
            text("SELECT id, lat, lon FROM place ORDER BY (kind = 'home') DESC, (id = 'home') DESC, name LIMIT :n"),
            {"n": limit})).all()  # fmt: skip
    return [(r[0], r[1], r[2]) for r in rows]


async def sync(engine: AsyncEngine, redis: Any = None) -> dict[str, Any]:
    s = get_settings()
    sources = [n for n, key in (("skiddle", s.skiddle_api_key), ("ticketmaster", s.ticketmaster_api_key)) if key]
    if not sources:
        return {"ok": False, "message": "no events source is set up (skiddle_api_key or ticketmaster_api_key)"}
    spots = await places(engine, s.events_sync_places)
    if not spots:
        return {"ok": False, "message": "no places saved yet"}
    zone = ZoneInfo(s.local_tz)
    today = datetime.now(zone).date()
    last = today + timedelta(days=s.events_sync_days)
    started = datetime.now(UTC)
    rows: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    calls = {"skiddle": 0}
    for place, lat, lon in spots:
        for source in sources:
            for page in range(max(1, s.events_sync_pages)):
                try:
                    if source == "skiddle":
                        if page or calls["skiddle"]:
                            await asyncio.sleep(SKIDDLE_GAP_S)  # stay under the 30 a minute the live page shares
                        calls["skiddle"] += 1
                        async with SkiddleClient(s.skiddle_api_key, s.skiddle_base_url or DEFAULT_BASE,
                                                 limiter=SlidingWindowLimiter(redis, "skiddle", 30) if redis else None) as c:
                            found = await c.search(lat, lon, radius_km=s.events_sync_radius_km, min_date=today, max_date=last,
                                                   limit=LIMIT, offset=page * LIMIT)  # fmt: skip
                    else:
                        async with TicketmasterClient(s.ticketmaster_api_key, s.ticketmaster_base_url or TM_BASE,
                                                      limiter=SlidingWindowLimiter(redis, "ticketmaster", 240) if redis else None) as c:
                            found = await c.search(
                                lat, lon, radius_km=s.events_sync_radius_km, limit=LIMIT, page=page,
                                start=datetime.combine(today, time.min, zone).astimezone(UTC),
                                end=datetime.combine(last, time.max, zone).astimezone(UTC))  # fmt: skip
                except (SkiddleError, TicketmasterError) as exc:
                    failures.append(f"{source} at {place}: {exc}")
                    break
                got = found.get("events", [])
                for e in got:
                    row = to_row(source, e, started)
                    if row:
                        rows[row["id"]] = row
                if len(got) < LIMIT or (page + 1) * LIMIT >= int(found.get("total") or 0):
                    break  # that was all of it
    async with engine.begin() as conn:
        if rows:
            await conn.execute(UPSERT, list(rows.values()))
            venues = {r["venue_id"]: {"id": r["venue_id"], "name": r.get("venue"), "town": r.get("town"),
                                      "postcode": r.get("postcode"), "lat": r["lat"], "lon": r["lon"], "seen": started}
                      for r in rows.values() if r.get("venue_id")}  # fmt: skip
            if venues:
                await conn.execute(VENUES, list(venues.values()))
        await conn.execute(text("DELETE FROM event_venue WHERE last_seen < :before"),
                           {"before": started - timedelta(days=VENUE_DAYS)})
        # Over, or no longer listed by a source that answered: dropped. A source that failed keeps what it had.
        answered = [x for x in sources if not any(f.startswith(x + " ") for f in failures)]
        gone = (await conn.execute(
            text("DELETE FROM local_event WHERE day < :today OR (seen < :started AND source = ANY(:answered))"),
            {"today": today, "started": started, "answered": answered})).rowcount
    return {"ok": True, "events": len(rows), "removed": gone, "places": len(spots), "failures": failures[:5]}


async def events_sync(ctx: dict[str, Any]) -> Any:
    """arq job: every six hours; returns at once without a key."""
    settings = get_settings()

    async def run() -> Any:
        if not settings.events_sync_enabled:
            return "disabled"
        return await sync(ctx["engine"], ctx.get("redis"))

    return await tracked(ctx["engine"], "events_sync", run)


async def _cli() -> int:
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        print(await sync(engine))
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli()))
