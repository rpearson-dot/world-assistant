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


@dataclass
class Use:
    km: float = 0.0  # ridden
    wear_km: float = 0.0  # with wet rides counted extra (for the kinds that wear faster in the wet)
    hours: float = 0.0
    rides: int = 0
    wet_rides: int = 0


def since_reset(part: dict[str, Any], services: list[dict[str, Any]]) -> datetime | None:
    """When the part's count starts: its fitting, or its last reset (a service, a charge, a top-up)."""
    resets = [s["at"] for s in services if s.get("part_id") == part["id"] and s.get("resets")]
    starts = [t for t in (part.get("fitted_at"), max(resets) if resets else None) if t]
    return max(starts) if starts else None


def use_of(part: dict[str, Any], rides: list[dict[str, Any]], placer: Placer, since: datetime | None,
           wet_factor: float = WET_FACTOR, trainer_has_cassette: bool = True) -> Use:  # fmt: skip
    """The rides a part was on a bike for since `since`."""
    kind = KINDS.get(part["kind"], KINDS["other"])
    u = Use()
    counts_indoors = kind.indoor and not (part["kind"] == "cassette" and trainer_has_cassette)
    for r in rides:
        if since and r["started"] < since:
            continue
        if r["indoor"] and not counts_indoors:
            continue
        if r.get("bike_id") is None or placer.bike_of_part(part["id"], r["started"]) != r["bike_id"]:
            continue
        wet = is_wet(r) and not r["indoor"]
        u.km += r["km"]
        u.wear_km += r["km"] * (wet_factor if wet and kind.wet else 1.0)
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
            "wet_rides": use.wet_rides, "since": since.isoformat() if since else None}  # fmt: skip


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
