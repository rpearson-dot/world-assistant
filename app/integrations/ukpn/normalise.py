"""UK Power Networks records made into power cuts, works, areas and carbon readings. Pure.

The portal's field names are not documented where this was written, and differ between datasets (`postcodesaffected`
in one, `postcode_affected` in another), so fields are found by what their names contain, the first match wins, and
the whole record is kept as `detail`. `python -m app.integrations.ukpn.probe` shows the real names.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

POSTCODE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b")


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def pick(rec: dict[str, Any], *wants: str | tuple[str, ...], avoid: tuple[str, ...] = ()) -> Any:
    """The first non-empty value whose field name contains a wanted word (a tuple: all of its words)."""
    keys = [(k, _norm(k)) for k in rec]
    for want in wants:
        words = (want,) if isinstance(want, str) else want
        for k, n in keys:
            if all(w in n for w in words) and not any(a in n for a in avoid):
                v = rec[k]
                if v not in (None, "", [], {}):
                    return v
    return None


def when(v: Any) -> datetime | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):  # epoch seconds or milliseconds
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, UTC)
    s = str(v).strip()
    for fmt in (None, "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            t = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
        except ValueError:
            continue
        return t if t.tzinfo else t.replace(tzinfo=UTC)
    return None


def _int(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def point(rec: dict[str, Any]) -> tuple[float | None, float | None]:
    """(lat, lon) from a geo_point_2d ({lon, lat}), a GeoJSON point, or plain latitude/longitude fields."""
    for v in rec.values():
        if isinstance(v, dict):
            if "lat" in v and "lon" in v:
                try:
                    return float(v["lat"]), float(v["lon"])
                except (TypeError, ValueError):
                    pass
            geom = v.get("geometry", v)
            if isinstance(geom, dict) and geom.get("type") == "Point":
                c = geom.get("coordinates") or []
                if len(c) >= 2:
                    return float(c[1]), float(c[0])
    lat, lon = pick(rec, "latitude", ("lat",), avoid=("plate", "lateral")), pick(rec, "longitude", ("lon",), ("lng",))
    try:
        return (float(lat), float(lon)) if lat is not None and lon is not None else (None, None)
    except (TypeError, ValueError):
        return None, None


def postcodes(v: Any) -> list[str]:
    text = " ".join(v) if isinstance(v, list) else str(v or "")
    found = [f"{a} {b}" for a, b in POSTCODE.findall(text.upper())]
    return list(dict.fromkeys(found))


def _id(rec: dict[str, Any], *wants: str | tuple[str, ...]) -> str:
    got = pick(rec, *wants)
    if got not in (None, ""):
        return str(got)
    return hashlib.sha1(repr(sorted((k, str(v)) for k, v in rec.items())).encode()).hexdigest()[:20]


def fault(rec: dict[str, Any], now: datetime) -> dict[str, Any]:
    kind = str(pick(rec, ("type",), avoid=("status", "postcode")) or "").lower()
    planned = True if "planned" in kind and "unplanned" not in kind else False if "unplanned" in kind or kind else None
    lat, lon = point(rec)
    return {
        "id": _id(rec, ("incident", "reference"), ("incident", "ref"), "reference", ("incident", "id")),
        "planned": planned,
        "status": _s(pick(rec, "status", avoid=("statusid",)) or pick(rec, "status")),
        "description": _s(pick(rec, "description", "message", "summary", "comment")),
        "postcodes": postcodes(pick(rec, "postcode")),
        "customers": _int(pick(rec, ("customer", "affected"), ("customers",), ("customer",))),
        "started_at": when(pick(rec, "creation", ("start", "time"), "start", "logged", "received")),
        "restore_at": when(pick(rec, "restor", "estimated")),
        "lat": lat, "lon": lon, "detail": rec, "seen": now,
    }  # fmt: skip


def works(rec: dict[str, Any], stage: str, now: datetime) -> dict[str, Any]:
    lat, lon = point(rec)
    return {
        "id": f"{stage}:" + _id(rec, ("permit", "ref"), ("works", "ref"), ("work", "ref"), "reference", "permit"),
        "stage": stage,
        "street": _s(pick(rec, ("street", "name"), "street", "road")),
        "town": _s(pick(rec, "town", "locality", "area", avoid=("areaname2",))),
        "usrn": _s(pick(rec, "usrn")),
        "category": _s(pick(rec, ("works", "category"), "category", ("activity", "type"))),
        "traffic": _s(pick(rec, ("traffic", "management"), "traffic")),
        "description": _s(pick(rec, "description", "summary", ("works", "type"))),
        "starts_at": when(pick(rec, ("actual", "start"), ("proposed", "start"), "start")),
        "ends_at": when(pick(rec, ("actual", "end"), ("proposed", "end"), ("estimated", "end"), "end", "finish")),
        "lat": lat, "lon": lon, "detail": rec, "seen": now,
    }  # fmt: skip


def area(rec: dict[str, Any], kind: str) -> dict[str, Any]:
    """One area a place sits in: a name and a code that identify it."""
    name = pick(rec, ("name",), ("site", "name"), ("substation",), ("sitename",), "description",
                avoid=("filename",))  # fmt: skip
    if kind == "rota":
        code = pick(rec, "block", "rota", "letter", "code", "id")
    else:
        code = pick(rec, ("site", "functional", "location"), ("functional", "location"), ("site", "id"), "code",
                    ("number",), "id", "reference")  # fmt: skip
    code = code if code not in (None, "") else (name or kind)
    return {"kind": kind, "code": str(code), "name": _s(name) or str(code)}


def carbon(rec: dict[str, Any]) -> tuple[str, datetime, float] | None:
    """(grid supply point, time, gCO2/kWh) from one carbon intensity record."""
    gsp = pick(rec, ("gsp", "name"), ("gsp",), ("grid", "supply"))
    at = when(pick(rec, "datetime", "timestamp", ("time",), ("date",), "period", "settlement"))
    value = pick(rec, ("carbon", "intensity"), "intensity", ("gco2",), ("co2",), "carbon")
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return (str(gsp), at, v) if gsp and at else None


def same_gsp(a: str, b: str) -> bool:
    """Grid supply point names as two datasets write them ('Wimbledon 132kV' and 'WIMBLEDON GSP')."""
    def core(x: str) -> str:
        x = re.sub(r"\b(gsp|grid supply point|\d+\s*kv|substation|s/s)\b", " ", x.lower())
        return re.sub(r"[^a-z0-9]", "", x)

    return bool(core(a)) and core(a) == core(b)


def _s(v: Any) -> str | None:
    if v in (None, ""):
        return None
    return " ".join(str(x) for x in v) if isinstance(v, list) else str(v)


def cleanest(readings: Iterable[tuple[datetime, float]], now: datetime, hours: float = 3.0) -> dict[str, Any] | None:
    """The cleanest block of `hours` from now on, and the reading now (half-hourly values)."""
    rows = sorted((t, v) for t, v in readings if t is not None)
    future = [(t, v) for t, v in rows if t >= now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)]
    if not future:
        return None
    n = max(1, round(hours * 2))
    best = min(range(max(1, len(future) - n + 1)), key=lambda i: sum(v for _, v in future[i:i + n]) / len(future[i:i + n]))
    block = future[best:best + n]
    return {"now": future[0][1], "now_at": future[0][0].isoformat(), "best_start": block[0][0].isoformat(),
            "best_average": round(sum(v for _, v in block) / len(block), 1), "hours": hours}  # fmt: skip
