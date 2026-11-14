"""forecast.solar: how much your solar panels should make, for each array (a "plane": one tilt, one direction, one
size), today and tomorrow, by the quarter or half hour. Free without a key: 12 requests an hour from one address.

GET https://api.forecast.solar/estimate/{lat}/{lon}/{declination}/{azimuth}/{kwp}?time=iso8601
  declination: tilt from flat, 0 to 90; azimuth: -180 to 180 with 0 south, -90 east, 90 west (people say 180 for
  south, 90 east, 270 west: `azimuth` converts); kwp: the array's peak kW.

The reading and sums are pure; `estimate` is the one network call.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

import httpx

BASE = "https://api.forecast.solar"


class SolarError(Exception):
    pass


def azimuth(compass: float) -> float:
    """forecast.solar's azimuth from a compass bearing (180 south, 90 east, 270 west)."""
    a = (float(compass) % 360) - 180
    return -180.0 if a == 180 else a


def _when(key: str, tz: Any) -> datetime | None:
    try:
        t = datetime.fromisoformat(key.replace(" ", "T"))
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=tz)).astimezone(UTC)


def periods(body: dict[str, Any], tz: Any) -> list[dict[str, Any]]:
    """Each period's start (UTC), the power at its start (W) and the energy in the period up to it (Wh).

    forecast.solar gives `watts` and `watt_hours_period` keyed by time; each Wh is the energy in the period that
    ends at its time, so a row is the period that starts at the previous time."""
    res = body.get("result") or {}
    watts, wh = res.get("watts"), res.get("watt_hours_period")
    if not isinstance(watts, dict) or not isinstance(wh, dict):
        raise SolarError("forecast.solar sent an answer without its forecast")
    times = sorted({t for k in set(watts) | set(wh) if (t := _when(k, tz))})
    by = {(_when(k, tz)): v for k, v in watts.items()}
    by_wh = {(_when(k, tz)): v for k, v in wh.items()}
    out, prev = [], None
    for t in times:
        if prev is not None:
            out.append({"start": prev, "end": t, "watts": float(by.get(prev) or 0), "wh": float(by_wh.get(t) or 0)})
        prev = t
    return out


def days(rows: list[dict[str, Any]], tz: Any) -> dict[str, float]:
    """kWh in each local day, from period rows (start, wh) of any number of arrays."""
    out: dict[str, float] = defaultdict(float)
    for r in rows:
        out[r["start"].astimezone(tz).date().isoformat()] += r["wh"] / 1000
    return {d: round(v, 2) for d, v in sorted(out.items())}


def hours(rows: list[dict[str, Any]], tz: Any, day: date) -> list[float]:
    """kWh in each local hour of a day (24 values), summed over the arrays."""
    out = [0.0] * 24
    for r in rows:
        local = r["start"].astimezone(tz)
        if local.date() == day:
            out[local.hour] += r["wh"] / 1000
    return [round(v, 3) for v in out]


def now_watts(rows: list[dict[str, Any]], at: datetime) -> float:
    """The forecast power now, summed over the arrays (linear between the two nearest times of each)."""
    total = 0.0
    by_plane: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_plane[r.get("plane", "")].append(r)
    for rs in by_plane.values():
        for r in rs:
            if r["start"] <= at < r["end"]:
                nxt = next((x for x in rs if x["start"] == r["end"]), None)
                span = (r["end"] - r["start"]).total_seconds() or 1
                f = (at - r["start"]).total_seconds() / span
                total += r["watts"] + ((nxt["watts"] if nxt else 0.0) - r["watts"]) * f
                break
    return round(total)


def remaining(rows: list[dict[str, Any]], at: datetime, tz: Any) -> float:
    """kWh still to come today (local), from now."""
    today = at.astimezone(tz).date()
    kwh = 0.0
    for r in rows:
        if r["start"].astimezone(tz).date() != today or r["end"] <= at:
            continue
        span = (r["end"] - r["start"]).total_seconds() or 1
        part = 1.0 if r["start"] >= at else (r["end"] - at).total_seconds() / span
        kwh += r["wh"] / 1000 * part
    return round(kwh, 2)


async def estimate(client: httpx.AsyncClient, lat: float, lon: float, plane: dict[str, Any], tz: Any) -> list[dict[str, Any]]:
    url = (f"{BASE}/estimate/{lat:.4f}/{lon:.4f}/{float(plane['declination']):g}/{azimuth(plane['azimuth']):g}/"
           f"{float(plane['kwp']):g}")  # fmt: skip
    r = await client.get(url, params={"time": "iso8601"}, timeout=30)
    if r.status_code == 429:
        raise SolarError("forecast.solar's hourly limit is used up (12 an hour from one address): it tries again next hour")
    if r.status_code >= 400:
        try:
            why = (r.json().get("message") or {}).get("text")
        except ValueError:
            why = None
        raise SolarError(f"forecast.solar refused the request (HTTP {r.status_code}{': ' + why if why else ''})")
    return [{**p, "plane": plane["name"]} for p in periods(r.json(), tz)]


def from_ha(entries: dict[str, Any]) -> list[dict[str, Any]]:
    """The arrays set up in Home Assistant's Forecast.Solar integration, from its .storage/core.config_entries:
    tilt, direction (Home Assistant uses compass bearings, 180 south) and size (modules_power, in W)."""
    out = []
    for e in ((entries.get("data") or {}).get("entries") or []):
        if e.get("domain") != "forecast_solar":
            continue
        o = {**(e.get("data") or {}), **(e.get("options") or {})}
        try:
            out.append({"name": (e.get("title") or "Solar")[:40], "declination": float(o["declination"]),
                        "azimuth": float(o["azimuth"]), "kwp": round(float(o["modules_power"]) / 1000, 3)})  # fmt: skip
        except (KeyError, TypeError, ValueError):
            continue
    return out
