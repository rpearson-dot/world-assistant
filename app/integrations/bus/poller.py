"""Scheduled bus polling: fetch a line's arrivals, follow each bus, store what is learned."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.config import get_settings
from app.integrations.bods.client import BodsError, Box, fetch_vehicles
from app.integrations.bus import bods_source, live, store
from app.integrations.bus.bods_source import BodsLineClient
from app.integrations.bus.config import TrackerConfig
from app.integrations.bus.events import (
    assign_slots,
    early_terminations,
    segment_observations,
    sentence,
    slots_in_window,
    target_hits,
    to_minutes,
    turnarounds,
)
from app.integrations.bus.trips import detect_anomalies, first_sightings, new_state, update_trips
from app.integrations.tfl.client import TflClient, TflError
from app.jobs import tracked
from app.ratelimit import RateLimitExceeded

log = logging.getLogger("wa.bus")


def in_window(cfg: TrackerConfig, local: datetime) -> bool:
    if cfg.weekdays_only and local.weekday() > 4:
        return False
    minutes = local.hour * 60 + local.minute
    return to_minutes(cfg.poll_start) <= minutes <= to_minutes(cfg.poll_end)


def local_epoch(day: date, hhmm: str, tz: ZoneInfo) -> float:
    h, m = hhmm.split(":")
    return datetime(day.year, day.month, day.day, int(h), int(m), tzinfo=tz).timestamp()


class Lookup:
    """Position of a stop within its direction's sequence."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.by_sid: dict[str, tuple[str, int]] = {}
        self.names: dict[str, str] = {}
        self.end_pos: dict[str, int] = {}  # position of the last stop of each direction
        self.towards: dict[str, str] = {}  # name of the last stop of each direction: "towards Kingston"
        for r in rows:
            if r["pos"] >= self.end_pos.get(r["direction"], -1) and r["name"]:
                self.towards[r["direction"]] = r["name"]
            self.end_pos[r["direction"]] = max(self.end_pos.get(r["direction"], 0), r["pos"])
            self.by_sid.setdefault(r["sid"], (r["direction"], r["pos"]))
            self.names.setdefault(r["sid"], r["name"] or "")

    def pos_of(self, sid: str) -> tuple[str, int] | None:
        return self.by_sid.get(sid)

    def dir_of(self, sid: str) -> str | None:
        w = self.by_sid.get(sid)
        return w[0] if w else None


def compute_hits(
    cfg: TrackerConfig, state: dict[str, Any], now: float, local_day: date, tz: ZoneInfo, slots: list[str]
) -> list[dict[str, Any]]:  # fmt: skip
    """Buses that reached the target in time and have not been reported yet, each with the scheduled
    slot it matches and how late it left. Mutates `state` (it remembers what it has reported)."""
    tcfg = cfg.target_cfg()
    if not tcfg:
        return []
    hits = target_hits(state, now, tcfg, local_epoch(local_day, cfg.earliest, tz), local_epoch(local_day, cfg.deadline, tz))
    claimed: set[str] = set(state.setdefault("claimed", []))
    hits = sorted(hits, key=lambda x: x["board_ts"])
    locals_ = [datetime.fromtimestamp(h["board_ts"], tz) for h in hits]
    chosen = assign_slots([t.hour * 60 + t.minute for t in locals_], slots, claimed)  # in order: buses do not overtake
    for h, board_local, slot in zip(hits, locals_, chosen, strict=True):
        if slot:
            claimed.add(slot)
        h["slot"] = slot
        h["delay_min"] = round(board_local.hour * 60 + board_local.minute - to_minutes(slot), 1) if slot else None
    state["claimed"] = sorted(claimed)
    return hits


def process_poll(
    cfg: TrackerConfig,
    state: dict[str, Any],
    preds: list[dict[str, Any]],
    lookup: Lookup,
    now: float,
    local_day: date,
    tz: ZoneInfo,
    slots: list[str],
) -> dict[str, Any]:
    """One poll's worth of logic with no I/O. Mutates `state`; returns what to store."""
    day_key = local_day.isoformat()
    if (
        state.get("day") != day_key
    ):  # new day: forget yesterday's reports and pending terminal buses
        state["day"] = day_key
        state["reported"] = []
        state["claimed"] = []
        state["terminal"] = {}
        state["turned"] = []
    update_trips(state, preds, lookup.pos_of, now)
    anomalies = detect_anomalies(state, lookup.dir_of, lookup.names, lookup.towards)
    anomalies += turnarounds(state, cfg.place_of, lookup.towards)
    anomalies += early_terminations(state, lookup.end_pos, lookup.names, towards=lookup.towards)
    anomalies += first_sightings(state, lookup.names)
    obs = segment_observations(state, now, cfg.segment_pairs())
    hits = compute_hits(cfg, state, now, local_day, tz, slots)
    return {"anomalies": anomalies, "observations": obs, "hits": hits}


def build_snapshot(
    cfg: TrackerConfig, hits: list[dict[str, Any]], slots: list[str], preds: list[dict[str, Any]],
    state: dict[str, Any], tz: ZoneInfo,
) -> dict[str, dict[str, Any]]:  # fmt: skip
    """Everything published to MQTT, as {sensor: {state, attributes}}. Pure."""
    hit_slots = {h["slot"] for h in hits if h.get("slot")}
    pending = [x for x in slots if x not in hit_slots]
    last = hits[-1] if hits else None
    snap: dict[str, dict[str, Any]] = {
        "last_bus": {
            "state": last["arrive_at"].astimezone(tz).strftime("%H:%M") if last else "none",
            "attributes": {
                "vehicle": last["vehicle"],
                "slot": last["slot"],
                "delay_min": last["delay_min"],
                "via_return": last["via_return"],
                "deadline": cfg.deadline,
            }
            if last
            else {"deadline": cfg.deadline},
        },
        "hits_today": {"state": len(hits), "attributes": {"slots": sorted(hit_slots)}},
        "pending_slots": {"state": len(pending), "attributes": {"slots": pending}},
        "active_buses": {"state": len(state.get("trips", {})), "attributes": {}},
        "summary": {
            "state": sentence(last, cfg.deadline, tz),
            "attributes": {"deadline": cfg.deadline},
        },
    }
    for stop in cfg.stops:
        rows = sorted(
            (p for p in preds if p.get("naptanId") == stop.sid),
            key=lambda p: p.get("timeToStation", 0),
        )
        mins = [round(p.get("timeToStation", 0) / 60) for p in rows[:3]]
        snap[f"next_{stop.key}"] = {
            "state": mins[0] if mins else None,
            "attributes": {"stop": stop.label, "next_minutes": mins,
                           "vehicles": [p.get("vehicleId") for p in rows[:3]]},
        }  # fmt: skip
    return snap


async def _sequences(
    conn: AsyncConnection, client: Any, tid: str, cfg: TrackerConfig
) -> list[dict[str, Any]]:
    rows = await store.load_sequences(conn, tid)
    if rows:
        return rows
    if cfg.sequences:
        seqs = {d: [{"id": s, "name": ""} for s in sids] for d, sids in cfg.sequences.items()}
    else:
        seqs = {d: await client.route_sequence(cfg.line, d) for d in ("outbound", "inbound")}
    await store.save_sequences(conn, tid, seqs)
    return await store.load_sequences(conn, tid)


async def _slots(
    conn: AsyncConnection, client: Any, tid: str, cfg: TrackerConfig
) -> list[str]:
    if not cfg.target:
        return []
    board = cfg.sid(cfg.target.board)
    times = await store.get_timetable(conn, tid, board)
    if times is None:
        try:
            times = await client.timetable_times(cfg.line, board, cfg.target.direction)
        except (TflError, BodsError) as exc:
            log.warning("timetable fetch failed: %s", exc)
            return []
        await store.save_timetable(conn, tid, board, times)
    alight = cfg.sid(cfg.target.alight)  # the scheduled times at the target stop, for the "due" column
    if await store.get_timetable(conn, tid, alight) is None:
        try:
            await store.save_timetable(conn, tid, alight, await client.timetable_times(cfg.line, alight, cfg.target.direction))
        except TflError as exc:
            log.warning("target timetable fetch failed: %s", exc)
    return slots_in_window(times, cfg.earliest, cfg.deadline, cfg.min_travel_min)


Publisher = Callable[[str, TrackerConfig, dict[str, dict[str, Any]]], Awaitable[None]]


async def _live_fixes(
    engine: AsyncEngine, cfg: TrackerConfig, state: dict[str, Any], preds: list[dict[str, Any]],
    lookup: Lookup, local: datetime,
) -> list[dict[str, Any]]:
    """GPS positions of the followed buses, only inside this tracker's journey window (see live.py)."""
    settings = get_settings()
    if not settings.bus_live_enabled or not settings.bods_api_key:
        return []
    if not live.in_journey_window(cfg, local.hour * 60 + local.minute):
        return []
    vehicles = live.known_vehicles(state, preds, {s.sid for s in cfg.stops})
    if not vehicles:
        return []
    sids = list(lookup.by_sid)  # the whole line, so a bus still approaching the route is in the box
    try:
        async with engine.connect() as conn:
            return await live.gather(conn, settings.bods_api_key, cfg, sids, vehicles)
    except Exception:
        log.exception("live positions failed for %s", cfg.line)
        return []


async def _bods_arrivals(
    engine: AsyncEngine, cfg: TrackerConfig, rows: list[dict[str, Any]], now: float
) -> list[dict[str, Any]]:
    """Predictions for a BODS-sourced line: the buses' GPS positions placed along the route's stops."""
    key = get_settings().bods_api_key
    async with engine.connect() as conn:
        coords = await store.stop_coords(conn, [r["sid"] for r in rows])
    routes: dict[str, list[bods_source.Stop]] = {}
    for r in sorted(rows, key=lambda x: (x["direction"], x["pos"])):
        if r["sid"] in coords:
            routes.setdefault(r["direction"], []).append(bods_source.Stop(r["sid"], r["name"] or "", *coords[r["sid"]]))
    pts = [(s.lat, s.lon) for stops in routes.values() for s in stops]
    if not pts:
        raise BodsError("the national stop register has none of this route's stops (has NaPTAN loaded yet?)")
    margin = 0.01
    box = Box(min(p[1] for p in pts) - margin, min(p[0] for p in pts) - margin,
              max(p[1] for p in pts) + margin, max(p[0] for p in pts) + margin)  # fmt: skip
    return bods_source.arrivals(await fetch_vehicles(key, box), routes, cfg.line, now)


async def poll_one(
    engine: AsyncEngine,
    client: Any,
    row: dict[str, Any],
    now: float,
    publisher: Publisher | None = None,
) -> str:
    tid = row["id"]
    cfg = TrackerConfig.model_validate(row["config"])
    tz = ZoneInfo(get_settings().local_tz)
    local = datetime.fromtimestamp(now, tz)
    if not in_window(cfg, local):
        return "outside window"
    async with engine.begin() as conn:
        seq_rows = await _sequences(conn, client, tid, cfg)
        lookup = Lookup(seq_rows)
        slots = await _slots(conn, client, tid, cfg)
        await store.ensure_day(conn, tid, local.date(), slots)
        current = await store.get_tracker(conn, tid)
    state = (current or row).get("state") or new_state()
    preds = await (_bods_arrivals(engine, cfg, seq_rows, now) if cfg.source == "bods" else client.line_arrivals(cfg.line))
    out = process_poll(cfg, state, preds, lookup, now, local.date(), tz, slots)
    fixes = [] if cfg.source == "bods" else await _live_fixes(engine, cfg, state, preds, lookup, local)
    live.attach(out["anomalies"], {v["vehicle"]: v for v in fixes}, lookup.towards)
    async with engine.begin() as conn:
        if fixes:
            await store.save_positions(conn, tid, fixes)
        if state.get("positions_pruned") != local.date().isoformat():
            await store.prune_positions(conn, live.KEEP_DAYS)
            state["positions_pruned"] = local.date().isoformat()
        await store.save_observations(conn, tid, out["observations"])
        await store.save_anomalies(conn, tid, out["anomalies"])
        await store.save_hits(conn, tid, local.date(), out["hits"])
        await store.save_state(conn, tid, state)
        day_hits = await store.day_hits(conn, tid, local.date())
    if publisher:
        await publisher(tid, cfg, build_snapshot(cfg, day_hits, slots, preds, state, tz))
    return f"{len(preds)} predictions, {len(out['hits'])} new hits"


async def poll_bus(ctx: dict[str, Any]) -> str:
    """arq job: poll every enabled tracker whose window is open. Silent when nothing is due."""
    settings = get_settings()
    if not settings.bus_poll_enabled:
        return "disabled"
    engine: AsyncEngine = ctx["engine"]
    now = time.time()
    tz = ZoneInfo(settings.local_tz)
    local = datetime.fromtimestamp(now, tz)
    async with engine.connect() as conn:
        rows = await store.list_trackers(conn, only_enabled=True)
        due = []
        for r in rows:
            try:
                if in_window(TrackerConfig.model_validate(r["config"]), local):
                    full = await store.get_tracker(conn, r["id"])
                    if full:
                        due.append(full)
            except ValueError:
                log.warning("tracker %s has an invalid config", r["id"])
    if not due:
        return "outside window"

    async def work() -> str:
        key = settings.tfl_app_key
        from app.ratelimit import SlidingWindowLimiter

        limiter = SlidingWindowLimiter(ctx["redis"], "tfl", 250 if key else 30)
        results = []

        async with TflClient(key, limiter=limiter) as client:
            for row in due:
                try:
                    conf = row["config"]
                    src = BodsLineClient(settings.bods_api_key, conf.get("operator")) if conf.get("source") == "bods" else client
                    results.append(
                        f"{row['id']}: {await poll_one(engine, src, row, now)}"
                    )
                except (TflError, BodsError, RateLimitExceeded) as exc:
                    log.warning("bus poll %s failed: %s", row["id"], exc)
                    results.append(f"{row['id']}: {type(exc).__name__}")
        return "; ".join(results)

    return await tracked(engine, "bus_poll", work)
