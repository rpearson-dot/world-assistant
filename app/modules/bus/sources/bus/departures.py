"""Next buses at the stops near a point, anywhere BODS reaches: what the bus tracker does for one line, for every line
seen nearby.

There are no countdowns outside London (TfL's Arrivals feed is London's), so the lines come from the buses BODS sees
within a few kilometres, each line's timetable from its operator's file on BODS (found and checked as the tracker
does, and kept a day), and each bus's minutes to a stop from where it is along that route (bods_source.arrivals: a
typical bus speed, so it does not see traffic). Where no bus is on its way yet, the timetable's next time is given
instead, on weekdays that are not bank holidays, since the timetables read here are weekday ones.

A timetable can take some seconds to fetch the first time, so it is fetched in the background: an answer never waits
more than a few seconds for one, and says which lines are still being looked up (they are there on the next ask).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.bankholidays import ensure as ensure_bank_holidays
from app.bankholidays import is_bank_holiday
from app.modules.bods.sources.bods.client import BodsError, box_around, fetch_vehicles
from app.modules.bods.sources.bods.txc import Route, TxcError
from app.modules.bus.sources.bus import bods_source as B
from app.modules.bus.sources.bus import store

log = logging.getLogger(__name__)
LONDON = ZoneInfo("Europe/London")
KEEP_S = 12 * 3600  # a timetable is kept this long
FAILED_S = 3600  # a line whose timetable could not be found is not tried again for this long
WAIT_S = 6.0  # the longest an answer waits for timetables still loading
MAX_LINES = 8
LINES_KM = 4.0  # buses this close say which lines run here

_routes: dict[tuple[str, str], tuple[float, Route | None, str | None]] = {}  # (noc, line) -> (when, route, why not)
_loading: dict[tuple[str, str], asyncio.Task[None]] = {}


def forget() -> None:
    _routes.clear()
    _loading.clear()


async def _load(engine: Any, api_key: str, noc: str, line: str, box: Any) -> None:
    async def coords_of(sids: list[str]) -> dict[str, tuple[float, float]]:
        async with engine.connect() as conn:
            return await store.stop_coords(conn, sids)

    try:
        route, _ = await B.load_route(api_key, line, noc, box, B.near_check(coords_of, box))
        _routes[(noc, line)] = (time.time(), route, None)
    except (BodsError, TxcError) as exc:
        _routes[(noc, line)] = (time.time(), None, str(exc))
    except Exception as exc:  # noqa: BLE001 - one line's file must not break the rest
        log.warning("bus timetable for %s %s failed: %s", noc, line, exc)
        _routes[(noc, line)] = (time.time(), None, f"{type(exc).__name__}")
    finally:
        _loading.pop((noc, line), None)


def _fresh(key: tuple[str, str]) -> bool:
    got = _routes.get(key)
    return bool(got) and time.time() - got[0] < (KEEP_S if got[1] is not None else FAILED_S)  # type: ignore[index]


def _next_times(route: Route, sid: str, direction: str, now: datetime, n: int = 2) -> list[str]:
    if now.weekday() >= 5 or is_bank_holiday(now.date()):  # the timetables read are weekday ones
        return []
    hm = now.strftime("%H:%M")
    return [t for t in route.times_at(sid, direction) if t > hm][:n]


async def departures(engine: Any, api_key: str, lat: float, lon: float, stops: list[dict[str, Any]],
                     now_ts: float | None = None, vehicles: list[dict[str, Any]] | None = None) -> dict[str, Any]:  # fmt: skip
    """For each stop (id, name, lat, lon), the buses on their way (minutes, live) and, failing that, the timetable's
    next times; plus which lines are still being looked up or have no timetable."""
    now_ts = now_ts or time.time()
    await ensure_bank_holidays(engine)
    box = box_around(lat, lon, LINES_KM)
    if vehicles is None:
        vehicles = await fetch_vehicles(api_key, box)
    counts = Counter((str(v.get("operator") or ""), str(v.get("line") or "")) for v in vehicles if v.get("line") and v.get("operator"))
    lines = [k for k, _ in counts.most_common(MAX_LINES)]
    for key in lines:
        if not _fresh(key) and key not in _loading:
            _loading[key] = asyncio.create_task(_load(engine, api_key, key[0], key[1], box))
    waiting = [t for k, t in _loading.items() if k in lines]
    if waiting:
        await asyncio.wait(waiting, timeout=WAIT_S)
    wanted = {s["id"] for s in stops}
    by_stop: dict[str, list[dict[str, Any]]] = {s["id"]: [] for s in stops}
    loading, missing = [], []
    now = datetime.fromtimestamp(now_ts, LONDON)
    for noc, line in lines:
        got = _routes.get((noc, line))
        if got is None:
            loading.append(line)
            continue
        route = got[1]
        if route is None:
            missing.append({"line": line, "operator": noc, "why": got[2]})
            continue
        seq = route.sequences()
        sids = sorted({x["id"] for xs in seq.values() for x in xs})
        async with engine.connect() as conn:
            coords = await store.stop_coords(conn, sids)
        routes = {d: [B.Stop(x["id"], x["name"], *coords[x["id"]]) for x in xs if x["id"] in coords] for d, xs in seq.items()}
        towards = {d: (xs[-1].name if xs else "") for d, xs in routes.items()}
        live = [p for p in B.arrivals([v for v in vehicles if v.get("operator") == noc], routes, line, now_ts) if p["naptanId"] in wanted]
        seen: set[tuple[str, str]] = set()
        for p in sorted(live, key=lambda p: p["timeToStation"]):
            by_stop[p["naptanId"]].append({"line": line, "towards": p.get("destinationName") or towards.get(p["direction"]),
                                           "minutes": max(round(p["timeToStation"] / 60), 0), "live": True,
                                           "vehicle": p["vehicleId"]})  # fmt: skip
            seen.add((p["naptanId"], p["direction"]))
        for d, xs in routes.items():
            for s in xs:
                if s.sid in wanted and (s.sid, d) not in seen and s.sid != (xs[-1].sid if xs else None):
                    times = _next_times(route, s.sid, d, now)
                    if times:
                        by_stop[s.sid].append({"line": line, "towards": towards[d], "at": times, "live": False})
    for v in by_stop.values():
        v.sort(key=lambda x: (not x["live"], x.get("minutes", 999), (x.get("at") or ["99:99"])[0]))
    return {"stops": [{**s, "buses": by_stop[s["id"]][:8]} for s in stops], "lines": [ln for _, ln in lines],
            "loading": sorted(set(loading)), "no_timetable": missing,
            "note": "Minutes are worked out from where each bus is along its route, at a typical speed (no traffic). "
                    "Timetable times are weekday ones, not given at weekends or on bank holidays."}  # fmt: skip
