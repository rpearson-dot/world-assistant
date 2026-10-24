"""TfL StopPoint data made into stations, their entrances and platforms, facilities and live closures. Pure.

`GET /StopPoint/Mode/{modes}` returns every stop point serving those modes, each with `children` (access areas,
entrances, platforms, sometimes other stations under a hub) and `additionalProperties` ({category, key, value}:
Facility, Accessibility, Zone, ...). `GET /StopPoint/Mode/{modes}/Disruption` returns what is closed now at those
stop points (lifts, escalators, entrances), each naming the stop and usually its station.

Written to TfL's published shapes but not run against the live API from where this was written, so it takes
what it recognises and skips the rest; `python -m app.integrations.tflstations.sync --probe` shows a real answer.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

MODES = ("tube", "dlr", "overground", "elizabeth-line", "tram")
STATION_TYPES = ("NaptanMetroStation", "NaptanRailStation", "NaptanFerryPort", "NaptanLiftCableCarStation")
HUB_TYPE = "TransportInterchange"


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f else None  # TfL gives 0 for "no position"


def _part_kind(stop_type: str) -> str | None:
    t = stop_type.lower()
    if "entrance" in t:
        return "entrance"
    if "platform" in t:
        return "platform"
    return None


def _lines(sp: dict[str, Any]) -> list[str]:
    out = []
    for ln in sp.get("lines") or []:
        name = (ln.get("name") or ln.get("id") or "") if isinstance(ln, dict) else str(ln)
        if name and name not in out:
            out.append(name)
    return out


def facilities(sp: dict[str, Any]) -> dict[str, dict[str, str]]:
    """additionalProperties as {category: {key: value}}."""
    out: dict[str, dict[str, str]] = {}
    for p in sp.get("additionalProperties") or []:
        if isinstance(p, dict) and p.get("key"):
            out.setdefault(str(p.get("category") or "Other"), {})[str(p["key"])] = str(p.get("value") or "")
    return out


def _walk(children: list[Any], station_id: str, parts: dict[str, dict[str, Any]]) -> None:
    for c in children or []:
        if not isinstance(c, dict):
            continue
        cid = c.get("naptanId") or c.get("id")
        kind = _part_kind(str(c.get("stopType") or ""))
        if kind and cid and cid not in parts:
            towards = facilities(c).get("Direction", {}).get("Towards") or c.get("indicator")
            parts[cid] = {"id": cid, "station_id": station_id, "kind": kind,
                          "name": c.get("commonName") or c.get("name") or cid, "lines": _lines(c),
                          "towards": towards, "lat": _num(c.get("lat")), "lon": _num(c.get("lon"))}  # fmt: skip
        _walk(c.get("children") or [], station_id, parts)


def _step_free(f: dict[str, dict[str, str]]) -> str | None:
    acc = f.get("Accessibility", {})
    for key in ("AccessViaLift", "AccessibleTo", "StepFreeAccess", "SpecificEntranceRequired"):
        if acc.get(key):
            return f"{key}: {acc[key]}"
    return None


def stations(payload: Any, now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(station rows, part rows) from a /StopPoint/Mode answer (a list, or {stopPoints: [...]})."""
    items = payload.get("stopPoints", []) if isinstance(payload, dict) else payload or []
    found: dict[str, dict[str, Any]] = {}
    parts: dict[str, dict[str, Any]] = {}

    def take(sp: dict[str, Any], hub: str | None) -> None:
        sid = sp.get("naptanId") or sp.get("id")
        lat, lon = _num(sp.get("lat")), _num(sp.get("lon"))
        if not sid or lat is None or lon is None:
            return
        f = facilities(sp)
        row = found.get(sid)
        if row is None:
            row = found[sid] = {"id": sid, "hub": sp.get("hubNaptanCode") or hub, "name": _clean(sp.get("commonName") or sid),
                                "modes": [], "lines": [], "zone": f.get("Geo", {}).get("Zone") or f.get("Zone", {}).get("Zone"),
                                "step_free": _step_free(f), "facilities": f, "lat": lat, "lon": lon, "seen": now}  # fmt: skip
        for m in sp.get("modes") or []:
            if m not in row["modes"]:
                row["modes"].append(m)
        for ln in _lines(sp):
            if ln not in row["lines"]:
                row["lines"].append(ln)
        _walk(sp.get("children") or [], sid, parts)

    for sp in items:
        if not isinstance(sp, dict):
            continue
        stop_type = str(sp.get("stopType") or "")
        if stop_type == HUB_TYPE:  # a hub: its stations are its children
            for c in sp.get("children") or []:
                if isinstance(c, dict) and str(c.get("stopType") or "") in STATION_TYPES:
                    take(c, sp.get("naptanId") or sp.get("id"))
        elif stop_type in STATION_TYPES:
            take(sp, None)
    for row in found.values():
        row["entrances"] = sum(1 for p in parts.values() if p["station_id"] == row["id"] and p["kind"] == "entrance")
        row["platforms"] = sum(1 for p in parts.values() if p["station_id"] == row["id"] and p["kind"] == "platform")
    return list(found.values()), [p for p in parts.values() if p["station_id"] in found]


_SUFFIX = re.compile(r"\s+(Underground|DLR|Rail|Tram Stop|Overground|ELL)\s+Station$|\s+(Station|Stop)$", re.IGNORECASE)


def _clean(name: str) -> str:
    """'Waterloo Underground Station' -> 'Waterloo'."""
    return _SUFFIX.sub("", name.strip()) or name.strip()


def alert_kind(text: str) -> str:
    t = text.lower()
    if "lift" in t:
        return "lift"
    if "escalator" in t:
        return "escalator"
    if "step-free" in t or "step free" in t:
        return "step_free"
    if "entrance" in t or "exit" in t:
        return "entrance"
    return "other"


def _when(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def alerts(payload: Any, now: datetime) -> list[dict[str, Any]]:
    """Closures now, from a /StopPoint/Mode/.../Disruption answer: one row per stop and text."""
    out: dict[str, dict[str, Any]] = {}
    for d in payload if isinstance(payload, list) else []:
        if not isinstance(d, dict):
            continue
        text = " ".join(str(d.get(k) or "") for k in ("description", "additionalInformation")).strip()
        station = d.get("stationAtcoCode") or d.get("atcoCode") or d.get("naptanId")
        if not text or not station:
            continue
        ends = _when(d.get("toDate"))
        if ends is not None and ends < now:
            continue
        rid = hashlib.sha1(f"{station}|{d.get('atcoCode')}|{text}".encode()).hexdigest()[:20]
        out[rid] = {"id": rid, "station_id": station, "name": _clean(str(d.get("commonName") or "")),
                    "kind": alert_kind(text), "description": text, "starts_at": _when(d.get("fromDate")),
                    "ends_at": ends, "seen": now}  # fmt: skip
    return list(out.values())
