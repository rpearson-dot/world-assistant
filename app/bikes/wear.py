"""How worn each part is: the rides it was on a bike for since it was last fitted or serviced, and what share of its
life that is. Pure: rows in, figures out, so every rule is testable without a database.

A part is on a bike at a moment if its latest fitting before then puts it on that bike, or in a set (a wheel, or any
labelled group) whose latest fitting before then puts the set on that bike. So swapping a wheelset moves the wear of
everything on it, and a part moved from one set to another keeps its own history.
"""

from __future__ import annotations

import itertools
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.bikes.defaults import KINDS, life_of

DUE_SHARE = 0.9  # due at 90% of a life...
DUE_LEFT = {"km": 300.0, "hours": 10.0, "days": 14.0}  # ...or within this of its end, whichever comes first
COMING_SHARE = 0.75
WET_FACTOR = 1.5
# a gravel or mountain-bike ride wears the drivetrain, pads and tyres faster than a road ride (grit and mud), counted
# like a wet one: its km times this (and times the wet factor too, when it was also wet)
SURFACE_FACTORS = {"gravel": 1.3, "mtb": 1.6}
ROUGH = {"tyre"}  # wears faster off-road though not in the wet; plus every kind that wears faster in the wet
GRAVEL = {"gravel_cycling", "cyclocross"}
MTB = {"mountain_biking", "enduro_mtb", "downhill_biking", "e_bike_mountain"}


class Placer:
    """Where every part and set was at any moment, from the fitting log."""

    def __init__(self, fits: list[dict[str, Any]]) -> None:
        self.log: dict[tuple[str, int], tuple[list[datetime], list[dict[str, Any]]]] = {}
        for f in sorted(fits, key=lambda f: f["at"]):
            times, rows = self.log.setdefault((f["item"], f["item_id"]), ([], []))
            times.append(f["at"])
            rows.append(f)

    def _at(self, item: str, item_id: int, t: datetime) -> dict[str, Any] | None:
        times, rows = self.log.get((item, item_id), ([], []))
        i = bisect_right(times, t)
        return rows[i - 1] if i else None

    def bike_of_set(self, set_id: int, t: datetime) -> int | None:
        f = self._at("set", set_id, t)
        return f["bike_id"] if f else None

    def bike_of_part(self, part_id: int, t: datetime) -> int | None:
        f = self._at("part", part_id, t)
        if not f:
            return None
        if f["bike_id"] is not None:
            return f["bike_id"]
        return self.bike_of_set(f["set_id"], t) if f["set_id"] is not None else None

    def now(self, item: str, item_id: int) -> dict[str, Any] | None:
        _, rows = self.log.get((item, item_id), ([], []))
        return rows[-1] if rows else None


def is_wet(ride: dict[str, Any]) -> bool:
    """What the rider said, or else the suggestion from the rain at the time."""
    return bool(ride["wet"]) if ride.get("wet") is not None else bool(ride.get("wet_suggested"))


def surface_of(ride: dict[str, Any], bike_type: str | None = None) -> str:
    """road, gravel or mtb: from Garmin's kind of ride ("Gravel Cycling", "Mountain Biking"); a plain "Cycling" on a
    mountain bike is taken as off-road, on anything else as road."""
    from app.bikes.garmin import kind_of

    k, _ = kind_of(ride.get("activity"))
    if k in GRAVEL:
        return "gravel"
    if k in MTB:
        return "mtb"
    if k in ("", "cycling") and bike_type in ("hardtail", "full_suspension"):
        return "mtb"
    return "road"


@dataclass
class Use:
    km: float = 0.0  # ridden
    wear_km: float = 0.0  # with wet and rough rides counted extra (for the kinds that wear faster for them)
    hours: float = 0.0
    rides: int = 0
    wet_rides: int = 0
    rough_rides: int = 0  # gravel or mountain-bike rides


def since_reset(part: dict[str, Any], services: list[dict[str, Any]]) -> datetime | None:
    """When the part's count starts: its fitting, or its last reset (a service, a charge, a top-up)."""
    resets = [s["at"] for s in services if s.get("part_id") == part["id"] and s.get("resets")]
    starts = [t for t in (part.get("fitted_at"), max(resets) if resets else None) if t]
    return max(starts) if starts else None


def use_of(part: dict[str, Any], rides: list[dict[str, Any]], placer: Placer, since: datetime | None,
           wet_factor: float = WET_FACTOR, trainer_has_cassette: bool = True,
           surface: dict[str, float] | None = None, bike_types: dict[int, str] | None = None,
           until: datetime | None = None) -> Use:  # fmt: skip
    """The rides a part was on a bike for since `since` (and before `until`)."""
    kind = KINDS.get(part["kind"], KINDS["other"])
    rough_kind = kind.wet or part["kind"] in ROUGH
    factors = SURFACE_FACTORS if surface is None else surface
    u = Use()
    counts_indoors = kind.indoor and not (part["kind"] == "cassette" and trainer_has_cassette)
    for r in rides:
        if since and r["started"] < since:
            continue
        if until and r["started"] >= until:
            continue
        if r["indoor"] and not counts_indoors:
            continue
        if r.get("bike_id") is None or placer.bike_of_part(part["id"], r["started"]) != r["bike_id"]:
            continue
        wet = is_wet(r) and not r["indoor"]
        rough = 1.0 if r["indoor"] else float(factors.get(surface_of(r, (bike_types or {}).get(r["bike_id"])), 1.0))
        u.km += r["km"]
        u.wear_km += r["km"] * (wet_factor if wet and kind.wet else 1.0) * (rough if rough_kind else 1.0)
        u.rough_rides += rough > 1.0
        u.hours += r["hours"] or 0.0
        u.rides += 1
        u.wet_rides += wet
    return u


def state(part: dict[str, Any], use: Use, since: datetime | None, now: datetime,
          due_share: float = DUE_SHARE, coming_share: float = COMING_SHARE) -> dict[str, Any]:  # fmt: skip
    """How much of its life a part has used, by each measure it has, and whether that is due or coming up."""
    life = {k: float(v) for k, v in (part.get("life") or life_of(part["kind"], part.get("details"))).items() if v}
    done = {"km": (part.get("start_km") or 0.0) + use.wear_km, "hours": (part.get("start_hours") or 0.0) + use.hours,
            "days": ((now - since).total_seconds() / 86400) if since else 0.0}  # fmt: skip
    measures = {}
    worst = 0.0
    is_due = False
    for unit, total in life.items():
        used = done[unit]
        share = used / total if total else 0.0
        left = total - used
        # "within 300 km" (or 10 hours, or a fortnight) only for lives well over twice that: a 250 km re-lube or a weekly
        # tyre check would otherwise always be due
        near_end = total >= 2 * DUE_LEFT[unit] and left <= DUE_LEFT[unit]
        due = share >= due_share or near_end or left <= 0
        measures[unit] = {"life": total, "used": round(used, 1), "share": round(share, 3), "left": round(left, 1),
                          "due": due}  # fmt: skip
        worst = max(worst, share)
        is_due = is_due or due
    st = "due" if is_due else "coming" if worst >= coming_share else "ok" if life else "untracked"
    return {"state": st, "share": round(worst, 3), "measures": measures, "km": round(use.km, 1),
            "wear_km": round(use.wear_km, 1), "hours": round(use.hours, 1), "rides": use.rides,
            "wet_rides": use.wet_rides, "rough_rides": use.rough_rides,
            "since": since.isoformat() if since else None}  # fmt: skip


def battery_health(part: dict[str, Any], services: list[dict[str, Any]], rides: list[dict[str, Any]],
                   placer: Placer, worn_share: float = 0.2) -> dict[str, Any]:  # fmt: skip
    """A labelled battery's charges, and its hours per full charge over time, from each charge that was logged with the
    level it was at (100% is taken as where the charge before left it). A battery giving `worn_share` fewer hours than
    in its first cycles is flagged as wearing. Judged over several cycles: a cold spell shortens one or two."""
    charges = sorted((s for s in services if s.get("part_id") == part["id"] and s["action"] == "charged"),
                     key=lambda s: s["at"])  # fmt: skip
    cycles = []
    for prev, cur in itertools.pairwise(charges):
        if cur.get("level_before") is None:
            continue
        used = (100.0 - float(cur["level_before"])) / 100.0
        if used < 0.3:  # too little of a charge used to say anything about the whole
            continue
        hours = sum(r["hours"] or 0.0 for r in rides if prev["at"] <= r["started"] < cur["at"]
                    and r.get("bike_id") is not None and placer.bike_of_part(part["id"], r["started"]) == r["bike_id"])  # fmt: skip
        if hours > 0:
            cycles.append({"at": cur["at"].isoformat(), "hours_per_charge": round(hours / used, 1)})
    out: dict[str, Any] = {"charges": len(charges), "cycles": cycles, "early": None, "recent": None, "wearing": False}
    if len(cycles) >= 6:
        early = sum(c["hours_per_charge"] for c in cycles[:3]) / 3
        recent = sum(c["hours_per_charge"] for c in cycles[-3:]) / 3
        out.update(early=round(early, 1), recent=round(recent, 1), wearing=recent < early * (1 - worn_share),
                   lost=round(1 - recent / early, 3) if early else None)  # fmt: skip
    if cycles:  # its own figure, once it has one, instead of the groupset's
        recent3 = cycles[-3:]
        out["learned_hours"] = round(sum(c["hours_per_charge"] for c in recent3) / len(recent3), 1)
    return out


def stretch_limit(part: dict[str, Any]) -> float:
    """When a chain is worn out (%): 0.5 for 11- and 12-speed (and a tandem's timing chain), 0.75 for 10 and below."""
    try:
        speeds = int(str((part.get("details") or {}).get("speeds") or 11).split()[0])
    except ValueError:
        speeds = 11
    return 0.5 if speeds >= 11 or part["kind"] == "timing_chain" else 0.75


def chain_wear(part: dict[str, Any], services: list[dict[str, Any]], rides: list[dict[str, Any]], placer: Placer,
               **use: Any) -> dict[str, Any] | None:  # fmt: skip
    """What a chain checker has said: each reading (%) at the km the chain had then (wet and rough rides counted extra,
    as its wear is), and from them how fast it is stretching and the km it will be worn out at. None with no readings.
    A straight line from new (0%) through the readings: chains stretch steadily once bedded in."""
    since = part.get("fitted_at")
    got = sorted((s for s in services if s.get("part_id") == part["id"] and s["action"] == "measured"
                  and s.get("reading") is not None and (since is None or s["at"] >= since)), key=lambda s: s["at"])  # fmt: skip
    if not got:
        return None
    limit = stretch_limit(part)
    readings = []
    for s in got:
        km = (part.get("start_km") or 0.0) + use_of(part, rides, placer, since, until=s["at"], **use).wear_km
        readings.append({"at": s["at"].isoformat(), "reading": float(s["reading"]), "km": round(km, 1)})
    pts = [(r["km"], r["reading"]) for r in readings if r["km"] >= 200]
    out: dict[str, Any] = {"limit": limit, "readings": readings, "latest": readings[-1]["reading"],
                           "worn": readings[-1]["reading"] >= limit, "rate_per_1000km": None, "life_km": None}  # fmt: skip
    sxx = sum(x * x for x, _ in pts)
    if sxx and any(y > 0 for _, y in pts):
        rate = sum(x * y for x, y in pts) / sxx  # % a km
        out["rate_per_1000km"] = round(rate * 1000, 3)
        out["life_km"] = round(limit / rate)
    return out
