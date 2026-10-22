"""World Assistant API gateway."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.assistant import router as assistant_router
from app.autodeploy import router as autodeploy_router
from app.backups import router as backups_router
from app.bods import router as bods_router
from app.bus import router as bus_router
from app.bus_page import page_router as bus_page_router
from app.config import get_settings
from app.energy.routes import router as energy_router
from app.events import page_router as events_page_router
from app.events import router as events_router
from app.floods import router as floods_router
from app.fuel import page_router as fuel_page_router
from app.fuel import router as fuel_router
from app.graph import store as graph_store
from app.graph_routes import router as graph_router
from app.ha_locations import router as ha_locations_router
from app.ha_routes import router as ha_routes_router
from app.icons import router as icons_router
from app.logging import configure_logging
from app.map_layers import router as map_router
from app.map_page import page_router as map_page_router
from app.places import page_router as places_page_router
from app.places import router as places_router
from app.pollen import page_router as pollen_page_router
from app.pollen import router as pollen_router
from app.postcodes import router as postcodes_router
from app.status import page_router
from app.status import router as status_router
from app.stops import router as stops_router
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

    await graph_store.close()
    await app.state.redis.aclose()
    await app.state.engine.dispose()


app = FastAPI(title="World Assistant", version="0.10.0", lifespan=lifespan)
# Files shipped with the app (Leaflet), so the pages load no third-party script. Read-only and
# unauthenticated: it holds nothing private, and StaticFiles refuses paths outside the directory.
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(status_router)
app.include_router(autodeploy_router)
app.include_router(backups_router)
app.include_router(events_router)
app.include_router(ha_locations_router)
app.include_router(ha_routes_router)
app.include_router(icons_router)
app.include_router(events_page_router)
app.include_router(pollen_router)
app.include_router(pollen_page_router)
app.include_router(places_router)
app.include_router(map_router)
app.include_router(stops_router)
app.include_router(floods_router)
app.include_router(energy_router)
app.include_router(postcodes_router)
app.include_router(bods_router)
app.include_router(graph_router)
app.include_router(map_page_router)
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
