"""Notifications from the environment, every half hour: a flood warning at one of your places (severe ones even in quiet hours), the air quality index high (7 or more) at one, very high pollen at home today (app/notify.py sends them; app/notify_jobs.py has the shared helpers).

Each is said once: by warning until it changes severity, by place and day for air and pollen. Several of the same kind go in one message.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import notify
from app.geoquery import POINT
from app.notify_jobs import page_link as _page

log = logging.getLogger(__name__)


async def environment_alerts(ctx: dict[str, Any]) -> Any:
    """The worker's half-hourly look for environment warnings at your places."""
    from app.jobs import tracked

    engine = ctx["engine"]

    async def run() -> Any:
        return await environment_check(engine)

    return await tracked(engine, "environment_alerts", run)


async def environment_check(engine: AsyncEngine, client: httpx.AsyncClient | None = None, today: date | None = None) -> dict[str, int]:
    from app.modules.environment.sources.environment import clients as C

    st = await notify.settings(engine)
    if not st["enabled"] or not st["kinds"].get("environment"):
        return {"skipped": 1}
    today = today or date.today()
    async with engine.connect() as c:
        places = (await c.execute(text("SELECT id, name, lat, lon FROM place ORDER BY (id = 'home') DESC, id LIMIT 12"))).all()
    out = {"floods": 0, "air": 0, "pollen": 0}
    # floods: live warnings whose area covers a place (or is within a km), most serious first
    for pid, name, lat, lon in places:
        try:
            async with engine.connect() as c:
                rows = (await c.execute(text(
                    "SELECT w.id, w.severity_level, w.severity, w.message, a.label FROM flood_warning w JOIN flood_area a ON a.notation = w.area "
                    f"WHERE w.severity_level <= 2 AND a.geom IS NOT NULL AND ST_DWithin(a.geom, {POINT}, 1000) "
                    "ORDER BY w.severity_level"), {"lat": lat, "lon": lon})).all()  # fmt: skip
        except Exception:  # noqa: BLE001 - no flood tables (floods switched off): nothing to say
            rows = []
        for w in rows:
            r = await notify.send(engine, "environment", f"flood:{w.id}:{w.severity_level}", f"{w.severity}: {name}",
                                  f"{w.label}. {(w.message or '')[:300]}", tags=["ocean", "warning"], urgent=w.severity_level == 1,
                                  priority=4, click=_page("/environment/floods"))  # fmt: skip
            out["floods"] += bool(r.get("sent"))
    # air: Defra's index 7 or more (high) at a place, once a day
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    try:
        for pid, name, lat, lon in places[:6]:
            key = f"air:{pid}:{today}"
            if await notify.sent(engine, key):
                continue
            try:
                a = await C.air_near(client, lat, lon)
            except C.EnvError:
                continue
            if a.get("index") and a["index"] >= 7:
                r = await notify.send(engine, "environment", key, f"Air pollution {a['band'].lower()} at {name}",
                                      f"Daily Air Quality Index {a['index']} ({a.get('worst')}). Cut down on strenuous activity outdoors "
                                      "if you have a lung or heart condition.", tags=["mask"], click=_page("/environment"))  # fmt: skip
                out["air"] += bool(r.get("sent"))
    finally:
        if own:
            await client.aclose()
    # pollen: very high (4) today for any allergen at home, from the stored grid (no extra requests)
    home = next(((n, la, lo) for pid, n, la, lo in places if pid == "home"), None)
    if home:
        try:
            from app.modules.pollen.pollen_grid import current, nearest
            from app.modules.pollen.pollen_grid import load as load_grid

            hit = nearest(current(await load_grid(engine)), home[1], home[2])
        except Exception:  # noqa: BLE001 - pollen switched off or no grid yet
            hit = None
        if hit:
            fc = hit[0]["forecast"]
            very = [a.get("name") for a in fc.get("allergens") or [] if (a.get("levels") or [0])[0] == 4]
            if very:
                r = await notify.send(engine, "environment", f"pollen:home:{today}", "Very high pollen today",
                                      f"{', '.join(str(x) for x in very)} at {home[0]}.", tags=["blossom"], click=_page("/pollen"))  # fmt: skip
                out["pollen"] += bool(r.get("sent"))
    return out
