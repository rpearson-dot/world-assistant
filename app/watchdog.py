"""World Assistant watching itself: every five minutes, from the api (one process at a time, by a Redis lease), it
checks that the worker is alive, that each scheduled job still runs and is not failing over and over, that the disks
have room, that there is a recent backup and that the last restore check passed. Each problem goes to the phone
once a day while it lasts ("World Assistant itself" notifications), and /v1/health/watch shows the latest check.

The restore check (a worker job, weekly) restores the newest backup into a scratch schema of the same database,
compares every table's rows with the backup's own count, and drops the schema: proof the backups would work.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import quantiles
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from app.auth import require_token

log = logging.getLogger("wa.watchdog")
router = APIRouter(prefix="/v1/health", tags=["status"], dependencies=[Depends(require_token)])
LEASE, LAST = "wa:watchdog:lease", "wa:watchdog:last"
EVERY_S = 300
HEARTBEAT_STALE = timedelta(minutes=12)
DISK_FULL = 90.0  # per cent
BACKUP_OLD = timedelta(hours=50)
RESTORE_KEY = "health.restore_check"
SCRATCH = "wa_restore_check"


# --- the checks (pure where they can be) ------------------------------------------------------------------------


def job_problems(runs: dict[str, list[tuple[datetime, str, str | None]]], now: datetime,
                 off: set[str] | frozenset[str] = frozenset()) -> list[dict[str, str]]:
    """Jobs that have stopped (silent for much longer than they have ever been) or keep failing.

    runs: job -> its runs, oldest first, as (started, status, error). A job with fewer than six runs is too new to
    judge; a job of a switched-off module (off) is expected to be silent."""
    out = []
    for job, rs in runs.items():
        if job in off or not rs:
            continue
        last3 = [r for r in rs if r[1] != "running"][-3:]
        if len(last3) == 3 and all(r[1] == "error" for r in last3):
            out.append({"key": f"failing:{job}", "text": f"{job} has failed {len(last3)} times in a row: {(last3[-1][2] or '')[:160]}"})
        if len(rs) < 6:
            continue
        gaps = [(b[0] - a[0]).total_seconds() for a, b in itertools.pairwise(rs)]
        p90 = quantiles(gaps, n=10)[-1] if len(gaps) >= 2 else gaps[0]
        allowed = max(1800.0, 3 * p90, 1.1 * max(gaps))
        quiet = (now - rs[-1][0]).total_seconds()
        if quiet > allowed:
            out.append({"key": f"stopped:{job}", "text": f"{job} has not run since {rs[-1][0].astimezone():%H:%M %d %b} "
                                                         f"(it usually runs at least every {_dur(p90)})"})  # fmt: skip
        if rs[-1][1] == "running" and quiet > 7200:
            out.append({"key": f"stuck:{job}", "text": f"{job} started {_dur(quiet)} ago and has not finished"})
    return out


def _dur(s: float) -> str:
    return f"{round(s / 60)} min" if s < 5400 else f"{round(s / 3600, 1):g} h" if s < 172800 else f"{round(s / 86400)} days"


def disk_problems(paths: dict[str, str]) -> list[dict[str, str]]:
    out = []
    for label, p in paths.items():
        try:
            u = shutil.disk_usage(p)
        except OSError:
            continue
        pct = 100 * u.used / u.total if u.total else 0
        if pct >= DISK_FULL:
            out.append({"key": f"disk:{label}", "text": f"The {label} disk is {pct:.0f}% full ({u.free / 1e9:.1f} GB free)"})
    return out


def backup_problems(newest: datetime | None, restore: dict[str, Any] | None, now: datetime) -> list[dict[str, str]]:
    out = []
    if newest is None:
        out.append({"key": "backup:none", "text": "There is no backup"})
    elif now - newest > BACKUP_OLD:
        out.append({"key": "backup:old", "text": f"The newest backup is from {newest.astimezone():%d %b %H:%M}"})
    if restore and not restore.get("ok"):
        out.append({"key": "restore:failed", "text": f"The last restore check failed: {restore.get('error') or restore.get('mismatches')}"})
    return out


# --- running them --------------------------------------------------------------------------------------------


async def _runs(engine: Any) -> dict[str, list[tuple[datetime, str, str | None]]]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT job, started_at, status, error FROM job_run WHERE started_at > now() - interval '14 days' "
            "ORDER BY job, started_at"))).all()  # fmt: skip
    out: dict[str, list[tuple[datetime, str, str | None]]] = {}
    for job, at, status, err in rows:
        out.setdefault(job, []).append((at, status, err))
    return out


async def _off_jobs(engine: Any) -> set[str]:
    from app.modules import registry
    from app.modules.gate import enabled

    on = await enabled(engine)
    # a switched-off module's jobs, and jobs that only run at times (Module.occasional), are not expected to run
    return {j for m in registry.modules() for j in m.jobs if m.id not in on or j in m.occasional}


def _newest_backup() -> datetime | None:
    from app import backup

    try:
        files = backup.list_backups()
    except Exception:  # noqa: BLE001 - no folder: no backups
        return None
    times = [datetime.fromisoformat(b["created"]) for b in files if b.get("created")]
    return max(times) if times else None


async def check(engine: Any, redis: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    problems: list[dict[str, str]] = []
    beat = await redis.get("wa:worker:heartbeat")
    beat_at = datetime.fromisoformat(beat) if beat else None
    if beat_at is None or now - beat_at > HEARTBEAT_STALE:
        problems.append({"key": "worker", "text": "The worker has stopped: nothing is being collected"
                         + (f" (last heard {beat_at.astimezone():%H:%M %d %b})" if beat_at else "")})  # fmt: skip
    problems += job_problems(await _runs(engine), now, await _off_jobs(engine))
    problems += disk_problems({"server": "/", "backups": "/backups"})
    async with engine.connect() as conn:
        v = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": RESTORE_KEY})).scalar()
        size = (await conn.execute(text("SELECT pg_database_size(current_database())"))).scalar()
    restore = json.loads(v) if isinstance(v, str) else v
    problems += backup_problems(_newest_backup(), restore, now)
    return {"checked_at": now.isoformat(), "problems": problems, "database_gb": round((size or 0) / 1e9, 2),
            "restore_check": restore, "worker_heartbeat": beat}  # fmt: skip


async def tell(engine: Any, problems: list[dict[str, str]]) -> None:
    from app import notify

    day = datetime.now(UTC).strftime("%Y%m%d")
    for p in problems:
        with contextlib.suppress(Exception):
            await notify.send(engine, "health", f"health:{p['key']}:{day}", "World Assistant needs a look", p["text"],
                              tags=["warning"], click="/settings")  # fmt: skip


class Watch:
    def __init__(self) -> None:
        self.me = uuid.uuid4().hex[:8]
        self.task: asyncio.Task[Any] | None = None

    async def start(self, app: Any) -> None:
        self.task = asyncio.create_task(self._run(app.state.engine, app.state.redis), name="watchdog")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()

    async def _run(self, engine: Any, redis: Any) -> None:
        await asyncio.sleep(90)  # let a starting stack settle first
        while True:
            try:
                mine = await redis.get(LEASE) == self.me or await redis.set(LEASE, self.me, ex=EVERY_S * 2, nx=True)
                if mine:
                    await redis.set(LEASE, self.me, ex=EVERY_S * 2)
                    out = await check(engine, redis)
                    await redis.set(LAST, json.dumps(out, default=str), ex=EVERY_S * 4)
                    await tell(engine, out["problems"])
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("watchdog: check failed", exc_info=True)
            await asyncio.sleep(EVERY_S)


WATCH = Watch()


@router.get("/watch")
async def watch(request: Request) -> dict[str, Any]:
    """The latest self-check: problems now (worker, jobs, disks, backups, restore check), database size, and the
    last restore check. Runs a check if none is recent."""
    raw = await request.app.state.redis.get(LAST)
    if raw:
        return json.loads(raw)
    return await check(request.app.state.engine, request.app.state.redis)


# --- the weekly restore check (worker) -----------------------------------------------------------------------


def restore_check_sync(conninfo: str | None = None, directory: Path | None = None) -> dict[str, Any]:
    """Restore the newest backup into a scratch schema, compare row counts with the backup's, drop the schema."""
    import tarfile

    import psycopg
    from psycopg import sql

    from app import backup

    files = backup.list_backups(directory)
    if not files:
        return {"ok": False, "error": "no backup to check"}
    newest = max(files, key=lambda b: b.get("created") or "")
    path = backup.path_for(newest["name"], directory)
    with tarfile.open(path, "r:gz") as tar:
        member = tar.extractfile("manifest.json")
        manifest = json.loads(member.read()) if member else {}
    src = manifest.get("schema") or "public"
    ci = conninfo or backup._conninfo()
    with psycopg.connect(ci, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(SCRATCH)))
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(SCRATCH)))
        try:
            have = {r[0] for r in conn.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND c.relkind IN ('r', 'p') AND NOT c.relispartition", (src,)).fetchall()}  # fmt: skip
            for t in list(manifest.get("tables", {})) + ["alembic_version"]:
                if t in have:
                    conn.execute(sql.SQL("CREATE TABLE {}.{} (LIKE {}.{} INCLUDING DEFAULTS)").format(
                        sql.Identifier(SCRATCH), sql.Identifier(t), sql.Identifier(src), sql.Identifier(t)))  # fmt: skip
            if "alembic_version" in have:
                conn.execute(sql.SQL("INSERT INTO {}.alembic_version VALUES (%s)").format(sql.Identifier(SCRATCH)),
                             (manifest.get("alembic_head"),))
            done = backup.restore(path, schema=SCRATCH, conninfo=ci, force=True)
        except Exception as exc:  # noqa: BLE001 - the check's answer
            return {"ok": False, "backup": newest["name"], "error": f"{type(exc).__name__}: {exc}"[:400]}
        finally:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(SCRATCH)))
    want = {t: v.get("rows") for t, v in manifest.get("tables", {}).items()}
    bad = {t: {"backup": want[t], "restored": done.get(t)} for t in want if done.get(t) != want[t]}
    return {"ok": not bad, "backup": newest["name"], "tables": len(done), "rows": sum(done.values()), "mismatches": bad or None}


async def restore_check(ctx: dict[str, Any]) -> Any:
    from app.jobs import tracked

    engine = ctx["engine"]

    async def work() -> dict[str, Any]:
        out = await asyncio.to_thread(restore_check_sync)
        out["at"] = datetime.now(UTC).isoformat()
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
                {"k": RESTORE_KEY, "v": json.dumps(out, default=str)})  # fmt: skip
        if not out["ok"]:
            await tell(engine, [{"key": "restore:failed", "text": f"The restore check failed: {out.get('error') or out.get('mismatches')}"}])
        return out

    return await tracked(engine, "restore_check", work)
