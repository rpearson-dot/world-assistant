"""arq worker: scheduled and background jobs live here."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.logging import configure_logging
from app.modules import registry
from app.modules.gate import gated
from app.ratelimit import SlidingWindowLimiter

log = logging.getLogger("wa.worker")


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    ctx["engine"] = create_async_engine(settings.database_url, pool_pre_ping=True)
    # One shared budget for every National Highways call, across all processes.
    ctx["limiter"] = SlidingWindowLimiter(ctx["redis"], "nh", settings.nh_rate_limit_per_minute)
    # Fuel Finder allows 100 requests a minute and one at a time; stay well inside it.
    ctx["fuel_limiter"] = SlidingWindowLimiter(ctx["redis"], "fuel", 60)
    await heartbeat(ctx)  # alive from the first second, not after the next five-minute mark
    await first_loads(ctx)
    log.info("world-assistant worker started (nh capture enabled=%s)", settings.nh_capture_enabled)


# Reference data that is otherwise loaded overnight: (table, job, the setting that turns it on).
FIRST_LOADS = (
    ("fuel_station", "fuel_sync_stations", "fuel_poll_enabled"),
    ("naptan_stop", "naptan_sync", "naptan_poll_enabled"),
    ("postcode", "codepoint_sync", "codepoint_poll_enabled"),
    ("boundary", "boundary_sync", "boundary_poll_enabled"),
)


async def first_loads(ctx: dict[str, Any]) -> list[str]:
    """On a new install, load what would otherwise wait for tonight's run (stations, stops, postcodes).

    Only for an empty table and a switched-on poll. The fixed job id means a restart does not queue it twice.
    Fuel stations without a Fuel Finder key just skip, as they do at night.
    """
    settings = get_settings()
    queued: list[str] = []
    for table, job, switch in FIRST_LOADS:
        if not getattr(settings, switch, False):
            continue
        try:
            async with ctx["engine"].connect() as conn:
                empty = not (await conn.execute(text(f"SELECT EXISTS (SELECT 1 FROM {table})"))).scalar_one()
        except Exception:  # a missing table (before migrations) must not stop the worker
            log.warning("could not check %s for a first load", table, exc_info=True)
            continue
        if empty and await ctx["redis"].enqueue_job(job, _job_id=f"wa:first-load:{job}", _defer_by=30):
            queued.append(job)
    if queued:
        log.info("first start: loading %s now rather than tonight", ", ".join(queued))
    return queued


async def shutdown(ctx: dict[str, Any]) -> None:
    from app.graph import store as graph_store

    await graph_store.close()
    await ctx["engine"].dispose()
    log.info("world-assistant worker stopping")


async def heartbeat(ctx: dict[str, Any]) -> None:
    """Proves the worker is alive and scheduling. Adapter jobs get added beside this."""
    await ctx["redis"].set("wa:worker:heartbeat", datetime.now(UTC).isoformat(), ex=600)


def _jobs() -> tuple[list[Any], list[Any]]:
    """Every module's worker jobs and their schedules, each wrapped so it stands down while its module is off."""
    settings = get_settings()
    functions: list[Any] = [heartbeat]
    crons: list[Any] = [cron(heartbeat, minute=set(range(0, 60, 5)))]
    for module_id, job in registry.worker_jobs():
        try:
            fn = gated(registry.import_string(job.func), module_id)
        except Exception:
            log.warning("job %s of module %s could not be loaded", job.func, module_id, exc_info=True)
            continue
        functions.append(fn)
        if job.cron is not None:
            spec = job.cron(settings) if callable(job.cron) else job.cron
            crons.append(cron(fn, **spec))
    return functions, crons


_FUNCTIONS, _CRONS = _jobs()


class WorkerSettings:
    functions: ClassVar[list] = _FUNCTIONS
    cron_jobs: ClassVar[list] = _CRONS
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_dsn)
    # Dedicated queue name so a shared Redis never mixes our jobs with another app's.
    queue_name = "wa:arq"
    health_check_interval = 30
    health_check_key = "wa:arq:health-check"
