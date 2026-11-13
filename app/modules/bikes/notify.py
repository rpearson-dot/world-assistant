"""Notifications from the bike garage: after each Garmin sync, batteries reported low (sensors, AXS), parts that have become due, a sensor seen for the first time, a device's software version changed; and the Garmin sync failing, or its key running out within 14 days (app/notify.py sends them; app/notify_jobs.py has the shared helpers).

Each is said once: by battery until it is next charged or changed, by part until it is next replaced or serviced. Several of the same kind go in one message.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import notify
from app.notify_jobs import page_link as _page
from app.notify_jobs import send_many as _send_many

log = logging.getLogger(__name__)


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
