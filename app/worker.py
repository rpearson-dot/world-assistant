"""arq worker: scheduled and background jobs live here."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.graph.build import graph_build
from app.ha_publish import ha_sync
from app.integrations.bus.poller import poll_bus
from app.integrations.fuelfinder.sync import fuel_sync_prices, fuel_sync_stations
from app.integrations.nationalhighways.capture import capture_job
from app.integrations.nationalhighways.poller import poll_live, poll_planned
from app.logging import configure_logging
from app.pollen_grid import pollen_grid_sync
from app.pollen_history import pollen_sync
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
    log.info("world-assistant worker started (nh capture enabled=%s)", settings.nh_capture_enabled)


async def shutdown(ctx: dict[str, Any]) -> None:
    from app.graph import store as graph_store

    await graph_store.close()
    await ctx["engine"].dispose()
    log.info("world-assistant worker stopping")


async def heartbeat(ctx: dict[str, Any]) -> None:
    """Proves the worker is alive and scheduling. Adapter jobs get added beside this."""
    await ctx["redis"].set("wa:worker:heartbeat", datetime.now(UTC).isoformat(), ex=600)


_LIVE_MIN = max(1, min(60, get_settings().nh_live_interval_min))
_FUEL_MIN = max(5, min(60, get_settings().fuel_price_interval_min))
_HA_MIN = max(1, min(60, get_settings().ha_interval_min))
_PLANNED_HOURS = max(1, min(24, get_settings().nh_planned_interval_hours))


class WorkerSettings:
    functions: ClassVar[list] = [
        heartbeat,
        capture_job,
        poll_live,
        poll_planned,
        fuel_sync_stations,
        fuel_sync_prices,
        poll_bus,
        pollen_sync,
        pollen_grid_sync,
        ha_sync,
        graph_build,
    ]
    cron_jobs: ClassVar[list] = [
        cron(heartbeat, minute=set(range(0, 60, 5))),
        # Bus tracker: every 30 s; the job returns at once outside each tracker's poll window.
        cron(poll_bus, hour=set(range(5, 11)), second={0, 30}, timeout=25, max_tries=1),
        # Every 20 minutes. A run is paced by the rate limiter, so allow it time.
        cron(capture_job, minute={0, 20, 40}, timeout=1500, max_tries=1),
        # Live feeds (offset from the capture run). Interval is WA_NH_LIVE_INTERVAL_MIN.
        cron(poll_live, minute=set(range(2, 60, _LIVE_MIN)), timeout=280, max_tries=1),
        # Planned works: many large pages, so allow it time. WA_NH_PLANNED_INTERVAL_HOURS.
        # Fuel prices every WA_FUEL_PRICE_INTERVAL_MIN minutes (a full pull takes ~4 minutes);
        # stations once a day.
        cron(fuel_sync_prices, minute=set(range(14, 60, _FUEL_MIN)), timeout=900, max_tries=1),
        cron(fuel_sync_stations, hour={3}, minute={20}, timeout=900, max_tries=1),
        # Pollen history for saved places, once a day (the provider allows 40 requests a day in all).
        cron(pollen_sync, hour={4}, minute={25}, timeout=300, max_tries=1),
        # Nationwide pollen lattice: the stalest points, twice a day (the provider allows 40 requests a day in all).
        cron(pollen_grid_sync, hour={3, 15}, minute={40}, timeout=3000, max_tries=1),
        # Home Assistant sensors. WA_HA_INTERVAL_MIN (default 5) must divide into the hour sensibly.
        cron(ha_sync, minute=set(range(1, 60, _HA_MIN)), timeout=120, max_tries=1),
        # Knowledge graph, rebuilt from Postgres twice an hour. It returns at once when switched off.
        cron(graph_build, minute={8, 38}, timeout=300, max_tries=1),
        cron(
            poll_planned,
            hour=set(range(0, 24, _PLANNED_HOURS)),
            minute={50},
            timeout=1500,
            max_tries=1,
        ),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_dsn)
    # Dedicated queue name so a shared Redis never mixes our jobs with another app's.
    queue_name = "wa:arq"
    health_check_interval = 30
    health_check_key = "wa:arq:health-check"
