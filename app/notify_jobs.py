"""What sends notifications (app/notify.py sends them):

- after each Garmin sync (bikes): batteries reported low (sensors, AXS), parts that have become due, a sensor seen for
  the first time; (firmware) a device's software version changed;
- the Garmin sync (system): failing, or the key running out within 14 days;
- every half hour (environment): a flood warning at one of your places (severe ones even in quiet hours), the air
  quality index high (7 or more) at one, very high pollen at home today.

Each is said once: by battery until it is next charged or changed, by part until it is next replaced or serviced, by
warning until it changes severity, by place and day for air and pollen. Several of the same kind after one sync go in
one message.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import notify
from app.geoquery import POINT

log = logging.getLogger(__name__)


async def _send_many(engine: AsyncEngine, kind: str, items: list[tuple[str, str]], one: str, many: str, tags: list[str],
                     click: str | None = None) -> int:  # fmt: skip
    """Items (key, line) not sent before, in one message: its title `one` for one item, `many` (with {n}) for more."""
    fresh = [(k, line) for k, line in items if not await notify.sent(engine, k)]
    if not fresh:
        return 0
    st = await notify.settings(engine)
    if not st["enabled"] or not st["kinds"].get(kind):
        return 0
    title = one if len(fresh) == 1 else many.format(n=len(fresh))
    r = await notify.send(engine, kind, fresh[0][0], title, "\n".join(line for _, line in fresh), tags=tags, click=click)
    if r.get("sent"):  # the others are said too: remembered so they are not said again
        async with engine.begin() as c:
            for k, line in fresh[1:]:
                await c.execute(text("INSERT INTO notify_log (key, kind, title, body, ok) VALUES (:k, :kind, :t, :b, true) "
                                     "ON CONFLICT (key) DO NOTHING"), {"k": k, "kind": kind, "t": title, "b": line})  # fmt: skip
    return len(fresh) if r.get("sent") else 0


def _page(path: str) -> str | None:
    from app.config import get_settings

    base = get_settings().ntfy_public_url
    return base.rsplit(":", 1)[0] + path if base else None  # the api is on the same device's 443


async def after_sync(engine: AsyncEngine, rider: str, firmware: list[dict[str, Any]] | None = None, now: datetime | None = None) -> dict[str, int]:
    """The bike notifications after one rider's Garmin sync."""
    from app.modules.bikes.api import _rider, build, load

    now = now or datetime.now(UTC)
    r = await _rider(engine, rider)
    g = build(await load(engine, rider), r, now)
    who = r["name"]
    low: list[tuple[str, str]] = []
    for b in g["bikes"]:
        for p in b["batteries"]:
            rp = p.get("reported") or {}
            if rp.get("level") is not None and rp["level"] <= 20:
                low.append((f"battery-low:{p['id']}:{(p.get('wear') or {}).get('since') or ''}",
                            f"{p['name']} on {b['label']}: {rp['status']}" + (f" ({rp['volts']} V)" if rp.get("volts") else "")))
    for p in g["kit"]:
        rp = p.get("reported") or {}
        if p["kind"] == "sensor" and rp.get("level") is not None and rp["level"] <= 20:
            low.append((f"battery-low:{p['id']}:{(p.get('wear') or {}).get('since') or ''}", f"{p['name']}: battery {rp['status']}"))
    due = []
    names = {b["id"]: b["label"] for b in g["bikes"]}
    for d in g["due"]:
        if d["state"] != "due" or d["kind"] in ("axs_battery", "sensor"):
            continue
        part = next((p for b in g["bikes"] for p in b["parts"] + [q for s in b["sets"] for q in s["parts"]] if p["id"] == d["part_id"]), None)
        since = ((part or {}).get("wear") or {}).get("since") or ""
        due.append((f"due:{d['part_id']}:{since}", f"{d['name']}" + (f" on {names[d['bike_id']]}" if d.get("bike_id") in names else "")
                    + (" (a spare is on the shelf)" if d.get("spare") else "")))
    new_sensors = [(f"sensor-new:{p['id']}", f"{p['name']} ({(p.get('details') or {}).get('maker') or ''})") for p in g["kit"]
                   if p["kind"] == "sensor" and (p.get("details") or {}).get("last")]  # fmt: skip
    out = {"low": await _send_many(engine, "bikes", low, f"{who}: battery low", f"{who}: {{n}} batteries low", ["battery"], _page("/bikes/garage")),
           "due": await _send_many(engine, "bikes", due, f"{who}: a part is due", f"{who}: {{n}} parts due", ["wrench"], _page("/bikes/garage"))}
    # a new sensor: said once per sensor; on the first sync after this was added every known sensor would be "new",
    # so those already there are only remembered
    first = not await notify.sent(engine, f"sensors-known:{rider}")
    if first:
        async with engine.begin() as c:
            for k, line in [(f"sensors-known:{rider}", "the sensors known when notifications began"), *new_sensors]:
                await c.execute(text("INSERT INTO notify_log (key, kind, title, body, ok) VALUES (:k, 'bikes', 'Sensors known', :b, true) "
                                     "ON CONFLICT (key) DO NOTHING"), {"k": k, "b": line})  # fmt: skip
    else:
        out["sensors"] = await _send_many(engine, "bikes", new_sensors, "New sensor seen", "{n} new sensors seen", ["satellite_antenna"])
    fw = [(f"firmware:{x['serial']}:{x['software']}", f"{x['maker']} {x.get('name') or x['type']}: {x['was']} → {x['software']}") for x in firmware or []]
    out["firmware"] = await _send_many(engine, "firmware", fw, "Firmware updated", "{n} devices updated", ["arrows_counterclockwise"])
    return out


async def garmin_system(engine: AsyncEngine, rider: str, error: str | None, today: date | None = None) -> None:
    """A Garmin sync that failed (once a day), and a key running out within 14 days (once per key)."""
    from app.modules.bikes.api import _rider
    from app.modules.bikes.garmin import status

    today = today or date.today()
    r = await _rider(engine, rider)
    if error:
        await notify.send(engine, "system", f"garmin-fail:{rider}:{today}", f"{r['name']}: Garmin sync failed", error, tags=["warning"])
    st = status(r, today)
    days = st.get("days_left")
    if st.get("key_expires") and days is not None and days <= 14:
        await notify.send(engine, "system", f"garmin-key:{rider}:{st['key_expires']}", f"{r['name']}: Garmin key runs out",
                          st.get("warn") or f"The Garmin key runs out on {st['key_expires']}.", tags=["key"])  # fmt: skip


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
