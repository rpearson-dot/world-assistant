"""UK Power Networks records made into power cuts, works and areas. Pure.

The portal's field names are not documented where this was written, and differ between datasets (`postcodesaffected`
in one, `postcode_affected` in another), so fields are found by what their names contain, the first match wins, and
the whole record is kept as `detail`. `python -m app.integrations.ukpn.probe` shows the real names.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

POSTCODE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b")
SECTOR = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s+(\d)(?![A-Z]{2}\b)")


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
    """Full postcodes and postcode sectors ('KT3 4ZZ' and 'KT3 4'), from whatever form they were published in
    (UK Power Networks gives sectors, as '[\\KT20 5\\"]"'). Every full postcode brings its sector too, so a place
    can be matched on either."""
    text = (" ".join(map(str, v)) if isinstance(v, list) else str(v or "")).upper()
    text = re.sub(r"[\\\"\[\];,]", " ", text)
    out = []
    for a, b in POSTCODE.findall(text):
        out += [f"{a} {b}", f"{a} {b[0]}"]
    out += [f"{a} {b}" for a, b in SECTOR.findall(text)]
    return list(dict.fromkeys(out))


def sector(postcode: str | None) -> str | None:
    m = POSTCODE.search((postcode or "").upper())
    return f"{m.group(1)} {m.group(2)[0]}" if m else None


def _id(rec: dict[str, Any], *wants: str | tuple[str, ...]) -> str:
    got = pick(rec, *wants)
    if got not in (None, ""):
        return str(got)
    return hashlib.sha1(repr(sorted((k, str(v)) for k, v in rec.items())).encode()).hexdigest()[:20]


DONE = {"complete", "completed", "restored", "closed", "resolved", "cancelled"}


def exact(rec: dict[str, Any], *names: str) -> Any:
    """The first non-empty value of a field named exactly one of `names` (ignoring case and punctuation)."""
    by = {_norm(k): v for k, v in rec.items()}
    for n in names:
        v = by.get(_norm(n))
        if v not in (None, "", [], {}):
            return v
    return None


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def fault(rec: dict[str, Any], now: datetime) -> dict[str, Any]:
    """A power cut. UK Power Networks' National Energy Outage dataset (as read in October 2026): fault_id,
    planned TRUE/FALSE, status (Complete once over), postcode sectors ("KT20 5"), confirmed_off and restored
    (customers), outage_start_date, etr (estimated restoration), date_of_restoration (actual)."""
    lat, lon = point(rec)
    planned_raw = exact(rec, "planned", "isplanned")
    if planned_raw is not None:
        planned: bool | None = str(planned_raw).strip().lower() in ("true", "yes", "1", "planned")
    else:
        kind = str(pick(rec, ("type",), avoid=("status", "postcode", "geometry")) or "").lower()
        planned = True if "planned" in kind and "unplanned" not in kind else False if kind else None
    status = _s(exact(rec, "status", "statusname") or pick(rec, "status", avoid=("statusid",)))
    off, back = _num(exact(rec, "confirmedoff", "customersoff")), _num(exact(rec, "restored", "customersrestored"))
    customers = int(off + back) if off or back else _int(pick(rec, ("customer", "affected"), ("customers",)))
    ended = when(exact(rec, "dateofrestoration", "restorationdate", "actualrestoration"))
    return {
        "id": str(exact(rec, "faultid", "incidentreference", "incidentref") or _id(rec, ("incident", "reference"))),
        "planned": planned,
        "status": status,
        "description": _s(exact(rec, "description", "incidentdescription", "message", "summary")),
        "postcodes": postcodes(exact(rec, "postcode", "postcodes", "postcodesaffected", "postcodeaffected")
                               or pick(rec, "postcode")),
        "customers": customers,
        "off_now": int(off) if off else None,
        "started_at": when(exact(rec, "outagestartdate", "creationdatetime", "plannedstart", "starttime")
                           or pick(rec, ("start", "date"), "creation", "logged")),
        "restore_at": when(exact(rec, "etr", "estimatedrestorationdate", "estimatedrestoration")
                           or pick(rec, ("estimated", "restor"))),
        "ended_at": ended,
        "active": not (status and status.lower() in DONE) and not (ended and ended <= now),
        "lat": lat, "lon": lon, "detail": rec, "seen": now,
    }  # fmt: skip


def _split(place: Any) -> tuple[str | None, str | None]:
    """'RUBENS CLOSE, BORDEN' -> ('Rubens Close', 'Borden')."""
    text = _s(place)
    if not text:
        return None, None
    street, _, town = (x.strip() for x in text.partition(","))
    tidy = lambda x: " ".join(w.capitalize() if w.isupper() else w for w in x.split()) or None
    return tidy(street), tidy(town)


def works(rec: dict[str, Any], stage: str, now: datetime) -> dict[str, Any]:
    """Street works. Open (as read): permit_ref, location 'STREET, TOWN', works_description, work_stream,
    actualstartdate, odp_end_date. Proposed: permit_ref, town 'STREET, AREA', works_description1, work_category,
    traffic_management_type1, proposedstartdate3, proposedenddate1."""
    lat, lon = point(rec)
    street = _s(exact(rec, "streetname", "street"))
    town = _s(exact(rec, "localityname", "locality"))
    if not street:
        street, rest = _split(exact(rec, "location", "town", "address"))
        town = town or rest
    return {
        "id": f"{stage}:" + str(exact(rec, "permitref", "permitreferencenumber", "permitreference")
                                or _id(rec, ("permit", "ref"), ("works", "ref"))),
        "stage": stage,
        "street": street,
        "town": town,
        "usrn": _s(exact(rec, "usrn")),
        "category": _s(exact(rec, "workstream", "workstream1", "workcategory", "workscategory")),
        "traffic": _s(exact(rec, "trafficmanagementtype", "trafficmanagementtype1") or pick(rec, ("traffic", "management"))),
        "description": _s(exact(rec, "worksdescription", "worksdescription1", "description", "activitytype")),
        "starts_at": when(exact(rec, "actualstartdate", "proposedstartdate3", "proposedstartdate", "startdate")
                          or pick(rec, ("start", "date"))),
        "ends_at": when(exact(rec, "actualenddate", "odpenddate", "proposedenddate1", "proposedenddate", "enddate")
                        or pick(rec, ("end", "date"))),
        "lat": lat, "lon": lon, "detail": rec, "seen": now,
    }  # fmt: skip


def area(rec: dict[str, Any], kind: str) -> dict[str, Any]:
    """One area a place sits in, with a name and a code (the site's functional location where there is one)."""
    if kind == "gsp":
        name = exact(rec, "gridsupplypoint", "gspname", "name")
        code = exact(rec, "gridsupplypointfloc", "gspfl", "gspfloc") or name
    elif kind == "primary":
        name = exact(rec, "primary", "sitename", "primaryname", "name")
        code = exact(rec, "primaryfloc", "sitefunctionallocation") or name
    elif kind == "secondary":
        code = exact(rec, "sitefunctionallocation", "sitefunctionallocationclean", "floc")
        alias = exact(rec, "substationalias", "sitename", "name")
        name = f"substation {alias}" if alias else None
    else:
        name = exact(rec, "name", "operator", "idno", "company") or pick(rec, ("name",))
        code = exact(rec, "code", "id") or name
    code = code if code not in (None, "") else (name or kind)
    return {"kind": kind, "code": str(code), "name": _s(name) or str(code)}


def _s(v: Any) -> str | None:
    if v in (None, ""):
        return None
    return " ".join(str(x) for x in v) if isinstance(v, list) else str(v)
