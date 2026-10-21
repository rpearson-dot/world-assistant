"""World Assistant API gateway."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.assistant import router as assistant_router
from app.autodeploy import router as autodeploy_router
from app.backups import router as backups_router
from app.bus import router as bus_router
from app.bus_page import page_router as bus_page_router
from app.config import get_settings
from app.events import page_router as events_page_router
from app.events import router as events_router
from app.fuel import page_router as fuel_page_router
from app.fuel import router as fuel_router
from app.ha_locations import router as ha_locations_router
from app.icons import router as icons_router
from app.logging import configure_logging
from app.places import page_router as places_page_router
from app.places import router as places_router
from app.pollen import page_router as pollen_page_router
from app.pollen import router as pollen_router
from app.status import page_router
from app.status import router as status_router
from app.tfl import page_router as tfl_page_router
from app.tfl import router as tfl_router
from app.vehicles import router as vehicles_router

log = logging.getLogger("wa.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    app.state.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    app.state.redis = Redis.from_url(settings.redis_dsn, decode_responses=True)

    if not settings.nh_primary_key:
        log.warning("nh_subscription_key is empty; National Highways calls will fail")

    log.info("world-assistant api started (env=%s)", settings.env)
    yield

    await app.state.redis.aclose()
    await app.state.engine.dispose()


app = FastAPI(title="World Assistant", version="0.7.4", lifespan=lifespan)
app.include_router(status_router)
app.include_router(autodeploy_router)
app.include_router(backups_router)
app.include_router(events_router)
app.include_router(ha_locations_router)
app.include_router(icons_router)
app.include_router(events_page_router)
app.include_router(pollen_router)
app.include_router(pollen_page_router)
app.include_router(places_router)
app.include_router(fuel_router)
app.include_router(bus_router)
app.include_router(assistant_router)
app.include_router(bus_page_router)
app.include_router(tfl_router)
app.include_router(tfl_page_router)
app.include_router(vehicles_router)
app.include_router(fuel_page_router)
app.include_router(places_page_router)
app.include_router(page_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Does not touch dependencies."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness: Postgres and Redis are reachable."""
    checks: dict[str, str] = {}

    try:
        async with request.app.state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        checks["postgres"] = f"error: {type(exc).__name__}"

    try:
        await request.app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        checks["redis"] = f"error: {type(exc).__name__}"

    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "degraded", "checks": checks},
    )
