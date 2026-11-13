"""The recorder's worker jobs.

- buslog_minute (every minute): when the recorder should be running (schedule.recording), it spends the minute asking
  BODS every 10 s, TfL's arrivals every 15 s and TfL's line status every 2 minutes, and stores every request and what
  came back, failed or empty included. The first minute of a day also takes the day's route and timetable.
- buslog_housekeep (nightly): the coming months' partitions, and dropping raw responses a school year old.

Polite: TfL through the shared "tfl" limiter (500 a minute is TfL's limit; this uses about 5); BODS asks no more often
than every 10 s (its own guidance: no more than every 5 s).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app import bankholidays
from app.config import get_settings
from app.jobs import tracked
from app.modules.buslog import parse, store
from app.modules.buslog.schedule import recording
from app.ratelimit import SlidingWindowLimiter

log = logging.getLogger("wa.buslog")

TFL = "https://api.tfl.gov.uk"
BODS = "https://data.bus-data.dft.gov.uk/api/v1/datafeed/"
BODS_EVERY, TFL_EVERY, STATUS_EVERY = 10, 15, 120
SPEND_S = 56  # of each minute; the next minute's run starts at :00
TIMEOUT_S = 9.0
# Kingston, New Malden, Worcester Park, Sutton, with room: until the route has been read once
DEFAULT_BOX = (-0.34, 51.34, -0.16, 51.43)
_locks: dict[str, asyncio.Lock] = {}


def _decode(v: Any) -> Any:
    return v.decode() if isinstance(v, bytes) else v


class Answer:
    """One request: what was asked (without the key), how it went and the body exactly as sent."""

    def __init__(self, url: str) -> None:
        self.url, self.status, self.error, self.body, self.content_type, self.ms = url, None, None, None, None, 0
        self.at = datetime.now(UTC)

    @property
    def ok(self) -> bool:
        return self.error is None and self.status == 200


async def ask(client: httpx.AsyncClient, url: str, params: dict[str, str], secret: dict[str, str]) -> Answer:
    """GET, recording the failure rather than raising. `secret` params are sent but never stored."""
    a = Answer(url + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else ""))
    t0 = time.monotonic()
    try:
        r = await client.get(url, params={**params, **secret}, timeout=TIMEOUT_S)
        a.status, a.body, a.content_type = r.status_code, r.content, r.headers.get("content-type")
        if r.status_code != 200:
            a.error = f"HTTP {r.status_code}"
    except httpx.HTTPError as exc:
        a.error = f"{type(exc).__name__}: {exc}"[:300] or type(exc).__name__
    a.ms = round((time.monotonic() - t0) * 1000)
    return a


class Recorder:
    def __init__(self, engine: Any, redis: Any, client: httpx.AsyncClient, line: str, day: date) -> None:
        s = get_settings()
        self.engine, self.redis, self.client, self.line, self.day = engine, redis, client, line, day
        self.tfl_key, self.bods_key = s.tfl_app_key, s.bods_api_key
        self.limiter = SlidingWindowLimiter(redis, "tfl", 250 if self.tfl_key else 30)
        self.counts: dict[str, list[int]] = {}  # source -> [ok, failed, empty]

    def _count(self, source: str, ok: bool, items: int | None) -> None:
        c = self.counts.setdefault(source, [0, 0, 0])
        c[0 if ok and items else 2 if ok else 1] += 1

    async def _store(self, source: str, a: Answer, items: int | None, error: str | None,
                     then: Callable[[Any, int], Awaitable[None]] | None = None) -> int:
        ok = a.ok and error is None
        async with self.engine.begin() as conn:
            pid = await store.poll(conn, at=a.at, day=self.day, line=self.line, source=source, url=a.url, status=a.status,
                                   ok=ok, error=error or a.error, ms=a.ms, items=items, body=a.body,
                                   content_type=a.content_type)  # fmt: skip
            if then is not None and ok:
                await then(conn, pid)
        self._count(source, ok, items)
        return pid

    async def _tfl(self, path: str, params: dict[str, str] | None = None) -> Answer:
        try:
            await self.limiter.acquire(max_wait=5)
        except Exception as exc:  # noqa: BLE001 - recorded as a failed request
            a = Answer(TFL + path)
            a.error = f"not asked: {exc}"
            return a
        return await ask(self.client, TFL + path, params or {}, {"app_key": self.tfl_key} if self.tfl_key else {})

    # --- every few seconds ---------------------------------------------------------------------------------------

    async def arrivals(self) -> None:
        a = await self._tfl(f"/Line/{self.line}/Arrivals")
        rows, err = None, None
        if a.ok:
            try:
                rows = parse.arrivals(json.loads(a.body or b"null"))
            except ValueError as exc:
                err = str(exc)
        key = f"wa:buslog:pred:{self.line}:{self.day}"
        async with _locks.setdefault(key, asyncio.Lock()):
            # a failed request changes nothing: no prediction is called "gone" because an answer did not come
            previous = json.loads(_decode(await self.redis.get(key)) or "{}") if rows is not None else {}
            diff, state = parse.changes(previous, rows) if rows is not None else ([], {})

            async def then(conn: Any, pid: int) -> None:
                await store.predictions(conn, pid, a.at, self.day, self.line, diff)

            await self._store("tfl-arrivals", a, len(rows) if rows is not None else None, err, then)
            if rows is not None:
                await self.redis.set(key, json.dumps(state), ex=36 * 3600)

    async def bods(self) -> None:
        if not self.bods_key:
            a = Answer(BODS)
            a.error = "no BODS key (Keys page)"
            await self._store("bods", a, None, None)
            return
        async with self.engine.connect() as conn:
            box = await store.route_box(conn, self.line)
        w, s, e, n = (box[0] - 0.02, box[1] - 0.015, box[2] + 0.02, box[3] + 0.015) if box else DEFAULT_BOX
        a = await ask(self.client, BODS, {"lineRef": self.line, "boundingBox": f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}"},
                      {"api_key": self.bods_key})
        rows, err = None, None
        if a.ok:
            try:
                rows = parse.positions(a.body or b"", self.line)
            except ValueError as exc:
                err = str(exc)

        async def then(conn: Any, pid: int) -> None:
            await store.positions(conn, pid, a.at, self.day, self.line, rows or [])

        await self._store("bods", a, len(rows) if rows is not None else None, err, then)

    async def status(self) -> None:
        a = await self._tfl(f"/Line/{self.line}/Status")
        st, err = None, None
        if a.ok:
            try:
                st = parse.status(json.loads(a.body or b"null"))
            except (ValueError, AttributeError) as exc:
                err = str(exc)

        async def then(conn: Any, pid: int) -> None:
            await store.status(conn, pid, a.at, self.day, self.line, st or {"severity": None, "reason": None, "disruptions": []})

        await self._store("tfl-status", a, len(st["disruptions"]) if st else None, err, then)

    # --- once a day ------------------------------------------------------------------------------------------------

    async def prepare(self) -> dict[str, Any]:
        """The day's route (both ways) and the timetable from each end, as they stood before the window."""
        out: dict[str, Any] = {}
        for direction in ("outbound", "inbound"):
            a = await self._tfl(f"/Line/{self.line}/Route/Sequence/{direction}")
            r, err = None, None
            if a.ok:
                try:
                    body = json.loads(a.body or b"null")
                    ids = [i for x in body.get("orderedLineRoutes") or [] for i in x.get("naptanIds") or []]
                    async with self.engine.connect() as conn:
                        pts = await store.naptan_points(conn, ids)
                    r = parse.route(body, pts)
                except (ValueError, AttributeError, TypeError) as exc:
                    err = f"route: {exc}"

            async def keep_route(conn: Any, pid: int, r: Any = r, d: str = direction, at: datetime = a.at) -> None:
                await store.route(conn, pid, at, self.day, self.line, d, r)

            await self._store("tfl-route", a, len(r["stops"]) if r else None, err, keep_route)
            out[direction] = len(r["stops"]) if r else None
            first = r["stops"][0]["stop"] if r and r["stops"] else None
            if not first:
                continue
            t = await self._tfl(f"/Line/{self.line}/Timetable/{first}", {"direction": direction})
            rows, err, chosen = None, None, None
            if t.ok:
                try:
                    chosen, rows = parse.timetable(json.loads(t.body or b"null"), self.day)
                except (ValueError, AttributeError, TypeError) as exc:
                    err = f"timetable: {exc}"
                if rows == [] and err is None:
                    err = "no schedule in TfL's timetable covers this day"

            async def keep_tt(conn: Any, pid: int, rows: Any = rows, d: str = direction, at: datetime = t.at) -> None:
                await store.timetable(conn, pid, at, self.day, self.line, d, rows or [])

            await self._store("tfl-timetable", t, len(rows) if rows is not None else None, err, keep_tt)
            out[direction + "_schedule"] = chosen
        return out

    # --- the minute -------------------------------------------------------------------------------------------------

    async def minute(self, started: float, prepared: bool) -> dict[str, Any]:
        done: dict[str, Any] = {}
        if not prepared:
            done["prepared"] = await self.prepare()
        plan: list[tuple[float, Callable[[], Awaitable[None]]]] = []
        plan += [(float(t), self.bods) for t in range(0, 60, BODS_EVERY)]
        plan += [(float(t) + 2, self.arrivals) for t in range(0, 60, TFL_EVERY)]
        if datetime.now(UTC).minute % (STATUS_EVERY // 60) == 0:
            plan.append((5.0, self.status))
        plan.sort(key=lambda p: p[0])
        tasks = []
        for offset, work in plan:
            if offset > SPEND_S:
                break
            wait = started + offset - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            if time.monotonic() - started > SPEND_S:
                break  # the day's first minute spent its start reading the route
            tasks.append(asyncio.create_task(work()))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors = [f"{type(r).__name__}: {r}" for r in results if isinstance(r, BaseException)]
        for e in errors:
            log.warning("bus log: %s", e)
        done["requests"] = {k: {"ok": v[0], "failed": v[1], "empty": v[2]} for k, v in self.counts.items()}
        if errors:
            done["errors"] = errors[:5]
        return done


async def buslog_minute(ctx: dict[str, Any]) -> Any:
    started = time.monotonic()
    engine, redis = ctx["engine"], ctx["redis"]
    tz = ZoneInfo(get_settings().local_tz)
    now = datetime.now(tz)
    async with engine.connect() as conn:
        st, cal, until = await store.load(conn)
    await bankholidays.ensure(engine)
    on, why = recording(now, st, cal, bankholidays.is_bank_holiday(now.date()), until)
    await redis.set("wa:buslog:state", json.dumps({"at": now.isoformat(), "recording": on, "why": why}), ex=600)
    if not on:
        return None  # not a job run: most minutes of the day are this
    day, line = now.date(), st["line"]
    flag = f"wa:buslog:prepared:{line}:{day}"
    prepared = bool(await redis.get(flag))
    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": "WorldAssistant bus log"}) as client:
        rec = Recorder(engine, redis, client, line, day)

        async def work() -> dict[str, Any]:
            out = await rec.minute(started, prepared)
            out["why"] = why
            return out

        result = await tracked(engine, "buslog", work)
    # taken once a day; a route that could not be read is tried again next minute
    if not prepared and any(isinstance(v, int) and v for v in (result.get("prepared") or {}).values()):
        await redis.set(flag, "1", ex=36 * 3600)
    return result


async def buslog_housekeep(ctx: dict[str, Any]) -> Any:
    async def work() -> dict[str, Any]:
        async with ctx["engine"].begin() as conn:
            return await store.housekeep(conn)

    return await tracked(ctx["engine"], "buslog_housekeep", work)
