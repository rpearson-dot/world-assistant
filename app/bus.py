"""Bus tracker API: configure trackers, see live buses, the target-stop run and its history."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings
from app.integrations.bus import store
from app.integrations.bus.config import TrackerConfig
from app.integrations.bus.events import (
    condition_summary,
    outlook,
    result_log,
    sentence,
    slot_reliability,
    to_minutes,
)
from app.integrations.bus.poller import Lookup, in_window
from app.integrations.bus.setup import SetupError, build_config, describe
from app.integrations.tfl.client import TflClient, TflError
from app.places import check_slug
from app.ratelimit import SlidingWindowLimiter

router = APIRouter(prefix="/v1/bus", tags=["bus"], dependencies=[Depends(require_token)])

HISTORY_DAYS = 30


class TrackerIn(BaseModel):
    config: TrackerConfig
    enabled: bool = True


def _tfl(request: Request) -> TflClient:
    key = get_settings().tfl_app_key
    return TflClient(
        key, limiter=SlidingWindowLimiter(request.app.state.redis, "tfl", 250 if key else 30)
    )


async def _sequences(request: Request, line: str) -> dict[str, list[dict[str, str]]]:
    if not re.fullmatch(r"[A-Za-z0-9-]{1,20}", line):
        raise HTTPException(422, "that does not look like a line name")
    try:
        async with _tfl(request) as c:
            seqs = {d: await c.route_sequence(line, d) for d in ("outbound", "inbound")}
    except TflError as exc:
        raise HTTPException(502, str(exc)) from exc
    if not any(seqs.values()):
        raise HTTPException(404, f"TfL has no stops for line {line!r}")
    return seqs


class SetupIn(BaseModel):
    line: str
    board: str = Field(description="stop id where you get on")
    target: str = Field(description="stop id where you need to get to, in the same direction")
    watch: list[str] = Field(default_factory=list, description="other stops to time on the way")
    earliest: str = Field("07:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    deadline: str = Field("08:10", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    name: str = "main"


_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class SettingsIn(BaseModel):
    tracker: str | None = Field(None, description="which tracker; optional when there is only one")
    deadline: str | None = Field(None, pattern=_HHMM, description="arrive by this time, e.g. 08:10")
    earliest: str | None = Field(None, pattern=_HHMM)
    show_until: str | None = Field(None, pattern=_HHMM, description="stop showing the chance after this time")

    @field_validator("tracker", "deadline", "earliest", "show_until", mode="before")
    @classmethod
    def _blank_is_unset(cls, v: Any) -> Any:  # Home Assistant sends "" for the fields it is not changing
        return None if isinstance(v, str) and not v.strip() else v


@router.post("/settings")
async def change_settings(request: Request, body: SettingsIn) -> dict[str, Any]:
    """Change the times of a tracker without touching its route or history. Meant for Home Assistant,
    so it answers {"ok", "message"} rather than an error code. History already recorded keeps the
    deadline it was recorded under; the new one applies from the next scheduled bus."""
    changes = body.model_dump(exclude_none=True, exclude={"tracker"})
    if not changes:
        return {"ok": False, "message": "nothing to change"}
    async with request.app.state.engine.begin() as conn:
        rows = await store.list_trackers(conn)
        if body.tracker:
            tid = body.tracker
        elif len(rows) == 1:
            tid = rows[0]["id"]
        else:
            return {"ok": False, "message": "say which tracker: " + ", ".join(r["id"] for r in rows) if rows else "no bus tracker is set up"}
        row = await store.get_tracker(conn, tid)
        if not row:
            return {"ok": False, "message": f"no tracker called {tid!r}"}
        cfg = {**row["config"], **changes}
        try:
            TrackerConfig.model_validate(cfg)
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        if to_minutes(cfg.get("earliest", "07:00")) >= to_minutes(cfg.get("deadline", "08:10")):
            return {"ok": False, "message": "the earliest time must be before the deadline"}
        await store.update_config(conn, tid, cfg)
    return {"ok": True, "tracker": tid, "message": "updated " + ", ".join(f"{k} {v}" for k, v in changes.items()), **changes}


@router.get("/lines/{line}/stops")
async def line_stops(request: Request, line: str) -> dict[str, Any]:
    """The stops on a line in each direction, for choosing a journey."""
    return describe(await _sequences(request, line))


@router.post("/setup")
async def setup(request: Request, body: SetupIn) -> dict[str, Any]:
    """Create or replace a tracker from plain choices; works out everything else."""
    check_slug(body.name)
    seqs = await _sequences(request, body.line)
    try:
        cfg = build_config(
            body.line, seqs, body.board, body.target, body.watch, body.earliest, body.deadline
        )
        TrackerConfig.model_validate(cfg)
    except (SetupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    async with request.app.state.engine.begin() as conn:
        await store.upsert_tracker(conn, body.name, cfg, True)
    names = {s["sid"]: s["label"] for s in cfg["stops"]}
    t = cfg["target"]
    return {"id": body.name, "direction": t["direction"], "board": names[cfg["stops"][0]["sid"]],
            "stops_tracked": [s["label"] for s in cfg["stops"]],
            "return_side_found": t["alight_return"] is not None}  # fmt: skip


@router.get("/trackers/{tid}/summary")
async def summary(request: Request, tid: str) -> dict[str, Any]:
    _, cfg = await _load(request, tid)
    return {
        "line": cfg.line,
        "stops": [s.label for s in cfg.stops],
        "earliest": cfg.earliest,
        "deadline": cfg.deadline,
    }


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().local_tz)


async def _load(request: Request, tid: str) -> tuple[dict[str, Any], TrackerConfig]:
    check_slug(tid)
    async with request.app.state.engine.connect() as conn:
        row = await store.get_tracker(conn, tid)
    if not row:
        raise HTTPException(404, "no such tracker")
    return row, TrackerConfig.model_validate(row["config"])


@router.put("/trackers/{tid}")
async def put_tracker(request: Request, tid: str, body: TrackerIn) -> dict[str, Any]:
    """Create or replace a tracker. Replacing restarts tracking and re-reads the route."""
    check_slug(tid)
    async with request.app.state.engine.begin() as conn:
        await store.upsert_tracker(conn, tid, body.config.model_dump(mode="json"), body.enabled)
    return {"id": tid, "enabled": body.enabled}


@router.get("/trackers")
async def get_trackers(request: Request) -> list[dict[str, Any]]:
    async with request.app.state.engine.connect() as conn:
        rows = await store.list_trackers(conn)
    return [
        {
            "id": r["id"],
            "enabled": r["enabled"],
            "line": r["config"].get("line"),
            "last_poll": r["state_updated"],
        }
        for r in rows
    ]


@router.get("/trackers/{tid}")
async def get_tracker(request: Request, tid: str) -> dict[str, Any]:
    row, _ = await _load(request, tid)
    return {
        "id": tid,
        "enabled": row["enabled"],
        "config": row["config"],
        "last_poll": row["state_updated"],
    }


@router.delete("/trackers/{tid}", status_code=204)
async def remove_tracker(request: Request, tid: str) -> None:
    check_slug(tid)
    async with request.app.state.engine.begin() as conn:
        if not await store.delete_tracker(conn, tid):
            raise HTTPException(404, "no such tracker")


@router.get("/trackers/{tid}/live")
async def live(request: Request, tid: str) -> dict[str, Any]:
    """Buses being followed right now, and the next arrivals at each stop of interest."""
    row, cfg = await _load(request, tid)
    async with request.app.state.engine.connect() as conn:
        lookup = Lookup(await store.load_sequences(conn, tid))
    now = time.time()
    active = []
    for trip in row["state"].get("trips", {}).values():
        last = trip["stops"][-1] if trip["stops"] else None
        active.append({
            "vehicle": trip["vehicle"], "direction": trip["dir"],
            "last_stop": lookup.names.get(last["sid"]) if last else None,
            "stops_seen": len(trip["stops"]),
        })  # fmt: skip
    try:
        async with _tfl(request) as c:
            preds = await c.line_arrivals(cfg.line)
    except TflError as exc:
        raise HTTPException(502, str(exc)) from exc
    next_at: dict[str, list[dict[str, Any]]] = {}
    for stop in cfg.stops:
        rows = sorted(
            (p for p in preds if p.get("naptanId") == stop.sid),
            key=lambda p: p.get("timeToStation", 0),
        )
        next_at[stop.key] = [
            {"label": stop.label, "vehicle": p.get("vehicleId"), "minutes": round(p.get("timeToStation", 0) / 60),
             "expected": p.get("expectedArrival"), "towards": p.get("destinationName")}
            for p in rows[:3]
        ]  # fmt: skip
    return {"as_of": now, "active": active, "next": next_at}


async def _history(request: Request, tid: str, since: Any) -> tuple[list[Any], list[Any]]:
    async with request.app.state.engine.connect() as conn:
        day_rows = (await conn.execute(
            text("SELECT day, slots FROM bus_target_day WHERE tracker = :t AND day >= :s ORDER BY day"),
            {"t": tid, "s": since})).all()  # fmt: skip
        hit_rows = (await conn.execute(
            text("SELECT day, slot, arrive_at, delay_min FROM bus_target_hit "
                 "WHERE tracker = :t AND day >= :s AND slot IS NOT NULL"),
            {"t": tid, "s": since})).all()  # fmt: skip
    return list(day_rows), list(hit_rows)


@router.get("/trackers/{tid}/target")
async def target(
    request: Request, tid: str, day: str | None = Query(None, pattern=r"^\d{4}-\d\d-\d\d$")
) -> dict[str, Any]:
    """The run to the target stop: which buses made it before the deadline, and which is the last.
    `outlook` is the chance each remaining bus gets there by the deadline (weekday mornings only)."""
    _, cfg = await _load(request, tid)
    if not cfg.target:
        raise HTTPException(409, "this tracker has no target configured")
    tz = _tz()
    now = datetime.now(tz)
    the_day = day or now.date().isoformat()
    async with request.app.state.engine.connect() as conn:
        d = (await conn.execute(
            text("SELECT slots FROM bus_target_day WHERE tracker = :t AND day = :d"),
            {"t": tid, "d": the_day})).first()  # fmt: skip
        hits = [dict(r._mapping) for r in await conn.execute(
            text("SELECT vehicle, board_at, arrive_at, slot, delay_min, via_return FROM bus_target_hit "
                 "WHERE tracker = :t AND day = :d ORDER BY arrive_at"),
            {"t": tid, "d": the_day})]  # fmt: skip
    hit_slots = {h["slot"] for h in hits if h["slot"]}
    slots = list(d[0]) if d else []
    pending = [s for s in slots if s not in hit_slots]
    last = hits[-1] if hits else None
    since = (now - timedelta(days=HISTORY_DAYS)).date()
    day_rows, hit_rows = await _history(request, tid, since)
    by_day: dict[str, list[str]] = {}
    for hday, slot, _a, _d in hit_rows:
        by_day.setdefault(hday.isoformat(), []).append(slot)
    reliability = slot_reliability(
        [{"date": dd.isoformat(), "slots": list(ss), "hit_slots": by_day.get(dd.isoformat(), [])} for dd, ss in day_rows],
        now.date().isoformat(), now.hour * 60 + now.minute,
    )  # fmt: skip
    chance = outlook(
        slots=slots, hit_slots=sorted(hit_slots), reliability=reliability,
        now_minutes=now.hour * 60 + now.minute, show_until=cfg.show_until,
        weekday_ok=not (cfg.weekdays_only and now.weekday() > 4),
    )  # fmt: skip
    return {
        "day": the_day, "earliest": cfg.earliest, "deadline": cfg.deadline,
        "board": cfg.target.board, "tracking_now": in_window(cfg, now), "scheduled": slots, "pending": pending,
        "hits": hits, "last_bus": last, "outlook": chance,
        "summary": sentence(last, cfg.deadline, tz),
    }  # fmt: skip


@router.get("/trackers/{tid}/log")
async def log(request: Request, tid: str, days: int = Query(HISTORY_DAYS, ge=1, le=90)) -> dict[str, Any]:
    """Success and failure of every scheduled bus on every recorded day: did a bus from the boarding
    stop reach the target (either side of the road) before the deadline?"""
    _, cfg = await _load(request, tid)
    tz = _tz()
    now = datetime.now(tz)
    day_rows, hit_rows = await _history(request, tid, (now - timedelta(days=days)).date())
    hits = [
        {"date": hday.isoformat(), "slot": slot, "arrive": arrive.astimezone(tz).strftime("%H:%M") if arrive else None,
         "delay_min": delay}
        for hday, slot, arrive, delay in hit_rows
    ]  # fmt: skip
    out = result_log([(dd.isoformat(), list(ss)) for dd, ss in day_rows], hits, now.date().isoformat(),
                     now.hour * 60 + now.minute)  # fmt: skip
    return {"deadline": cfg.deadline, "window_days": days, **out}


def _clock(epoch_s: Any, tz: ZoneInfo) -> str | None:
    try:
        return datetime.fromtimestamp(float(epoch_s), tz).strftime("%H:%M")
    except (TypeError, ValueError):
        return None


@router.get("/trackers/{tid}/terminations")
async def terminations(request: Request, tid: str, days: int = Query(7, ge=1, le=60)) -> dict[str, Any]:
    """A daily log of buses that ended a trip short of the end of the route while the tracker was
    watching: where they were last seen and when, whether they turned back (and where they next
    appeared) or just stopped appearing. Newest day first."""
    _, cfg = await _load(request, tid)
    tz = _tz()
    now = datetime.now(tz)
    since = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    async with request.app.state.engine.connect() as conn:
        rows = (await conn.execute(
            text("SELECT at, vehicle, detail FROM bus_anomaly WHERE tracker = :t AND type = 'early_termination' "
                 "AND at >= :s ORDER BY at"),
            {"t": tid, "s": since})).all()  # fmt: skip
    lo, hi = to_minutes(cfg.earliest), to_minutes(cfg.deadline)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for at, vehicle, d in rows:
        seen = _clock(d.get("at"), tz) or at.astimezone(tz).strftime("%H:%M")
        resumed = _clock(d.get("resumed_at"), tz)
        mins = to_minutes(seen)
        by_day.setdefault(at.astimezone(tz).date().isoformat(), []).append({
            "time": seen, "vehicle": vehicle, "kind": d.get("kind"), "direction": d.get("direction"), "towards": d.get("towards"),
            "stop": d.get("stop"), "stops_before_end": d.get("stops_before_end"),
            "resumed_at_stop": d.get("resumed_stop"), "resumed_at_time": resumed,
            "in_target_window": lo <= mins <= hi,
        })  # fmt: skip
    out = [{"date": k, "count": len(v), "in_target_window": sum(1 for e in v if e["in_target_window"]), "buses": v}
           for k, v in sorted(by_day.items(), reverse=True)]  # fmt: skip
    return {"window": {"earliest": cfg.earliest, "deadline": cfg.deadline}, "days": out}


@router.get("/trackers/{tid}/reliability")
async def reliability(
    request: Request, tid: str, days: int = Query(HISTORY_DAYS, ge=1, le=90)
) -> dict[str, Any]:
    """How often each scheduled slot actually produced a bus at the target stop."""
    _, cfg = await _load(request, tid)
    tz = _tz()
    now = datetime.now(tz)
    since = (now - timedelta(days=days)).date()
    async with request.app.state.engine.connect() as conn:
        day_rows = (await conn.execute(
            text("SELECT day, slots FROM bus_target_day WHERE tracker = :t AND day >= :s ORDER BY day"),
            {"t": tid, "s": since})).all()  # fmt: skip
        hit_rows = (await conn.execute(
            text("SELECT day, slot FROM bus_target_hit WHERE tracker = :t AND day >= :s AND slot IS NOT NULL"),
            {"t": tid, "s": since})).all()  # fmt: skip
    hit_by_day: dict[str, list[str]] = {}
    for d, slot in hit_rows:
        hit_by_day.setdefault(d.isoformat(), []).append(slot)
    history = [{"date": d.isoformat(), "slots": list(s), "hit_slots": hit_by_day.get(d.isoformat(), [])}
               for d, s in day_rows]  # fmt: skip
    return {
        "days_recorded": len(history),
        "deadline": cfg.deadline,
        "slots": slot_reliability(history, now.date().isoformat(), now.hour * 60 + now.minute),
    }


@router.get("/trackers/{tid}/conditions")
async def conditions(request: Request, tid: str) -> list[dict[str, Any]]:
    """Leg times today, last 30 minutes against the day's average."""
    _, cfg = await _load(request, tid)
    tz = _tz()
    start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    names = {s.sid: s.label for s in cfg.stops}
    out = []
    async with request.app.state.engine.connect() as conn:
        for a, b in cfg.segment_pairs():
            rows = (await conn.execute(
                text("SELECT extract(epoch FROM observed_at) AS observed_at, seconds FROM bus_segment_obs "
                     "WHERE tracker = :t AND from_stop = :a AND to_stop = :b AND observed_at >= :s"),
                {"t": tid, "a": a, "b": b, "s": start})).all()  # fmt: skip
            obs = [{"observed_at": float(r[0]), "seconds": float(r[1])} for r in rows]
            out.append(
                {
                    "from": names.get(a, a),
                    "to": names.get(b, b),
                    **condition_summary(obs, time.time()),
                }
            )
    return out


@router.get("/trackers/{tid}/anomalies")
async def anomalies(
    request: Request, tid: str, limit: int = Query(50, ge=1, le=500), type: str | None = None
) -> list[dict[str, Any]]:
    """Recent anomalies. The bookkeeping `first_seen` entries are left out unless asked for with `?type=first_seen`."""
    await _load(request, tid)
    async with request.app.state.engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT at, vehicle, type, detail FROM bus_anomaly WHERE tracker = :t "
                "AND (CAST(:ty AS text) IS NULL AND type <> 'first_seen' OR type = CAST(:ty AS text)) "
                "ORDER BY at DESC LIMIT :n"
            ),
            {"t": tid, "n": limit, "ty": type},
        )
        return [dict(r._mapping) for r in res]  # fmt: skip


@router.post("/trackers/{tid}/refresh-route")
async def refresh_route(request: Request, tid: str) -> dict[str, str]:
    """Forget the stored stop order and timetable so the next poll re-reads them from TfL."""
    await _load(request, tid)
    async with request.app.state.engine.begin() as conn:
        await conn.execute(text("DELETE FROM bus_stop WHERE tracker = :t"), {"t": tid})
        await conn.execute(text("DELETE FROM bus_timetable WHERE tracker = :t"), {"t": tid})
    return {"status": "will refresh on the next poll"}
