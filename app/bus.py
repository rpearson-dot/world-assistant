"""Bus tracker API: configure trackers, see live buses, the target-stop run and its history."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.config import get_settings
from app.integrations.bus import store
from app.integrations.bus.config import TrackerConfig
from app.integrations.bus.events import condition_summary, sentence, slot_reliability
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


@router.get("/trackers/{tid}/target")
async def target(
    request: Request, tid: str, day: str | None = Query(None, pattern=r"^\d{4}-\d\d-\d\d$")
) -> dict[str, Any]:
    """The run to the target stop: which buses made it before the deadline, and which is the last."""
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
    return {
        "day": the_day, "earliest": cfg.earliest, "deadline": cfg.deadline,
        "tracking_now": in_window(cfg, now), "scheduled": slots, "pending": pending,
        "hits": hits, "last_bus": last,
        "summary": sentence(last, cfg.deadline, tz),
    }  # fmt: skip


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
    request: Request, tid: str, limit: int = Query(50, ge=1, le=500)
) -> list[dict[str, Any]]:
    await _load(request, tid)
    async with request.app.state.engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT at, vehicle, type, detail FROM bus_anomaly WHERE tracker = :t ORDER BY at DESC LIMIT :n"
            ),
            {"t": tid, "n": limit},
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
