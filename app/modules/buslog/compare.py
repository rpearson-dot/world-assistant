"""A bus against its timetable, and a stop's next hour. Pure: given TfL's latest predictions, the day's timetable and
the route, so it is tested on its own.

Which scheduled journey a bus is running is not something TfL says, so it is worked out: the journey in the bus's
direction whose times at the stops the bus is predicted at are closest to the predictions (on average). A bus is
"on time" within a minute either way, else early ("fast") or late ("slow") by the difference.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

ON_TIME_S = 60
FURTHEST_S = 45 * 60  # a journey more than this from every prediction is not the one being run


def verdict(diff_s: float | None) -> str | None:
    if diff_s is None:
        return None
    if abs(diff_s) <= ON_TIME_S:
        return "on time"
    return "early" if diff_s < 0 else "late"


def words(diff_s: float | None) -> str:
    v = verdict(diff_s)
    if v is None:
        return "no prediction"
    if v == "on time":
        return "on time"
    m = round(abs(diff_s) / 60)  # type: ignore[arg-type]
    return f"{m} min {'early' if v == 'early' else 'late'}"


def journeys(rows: list[dict[str, Any]], direction: str) -> dict[int, dict[str, datetime]]:
    """The timetable rows of one direction as {journey: {stop: scheduled}}."""
    out: dict[int, dict[str, datetime]] = {}
    for r in rows:
        if r["direction"] == direction:
            out.setdefault(r["journey"], {})[r["stop"]] = r["scheduled_at"]
    return out


def which_journey(preds: dict[str, datetime], sched: dict[int, dict[str, datetime]]) -> int | None:
    """The scheduled journey closest to a bus's predictions (stop -> expected)."""
    best, best_cost = None, None
    for j, times in sched.items():
        diffs = [abs((preds[s] - t).total_seconds()) for s, t in times.items() if s in preds]
        if not diffs:
            continue
        cost = sum(diffs) / len(diffs)
        if cost <= FURTHEST_S and (best_cost is None or cost < best_cost):
            best, best_cost = j, cost
    return best


def bus_run(vehicle: str, preds: list[dict[str, Any]], route_stops: list[dict[str, Any]],
            timetable: list[dict[str, Any]]) -> dict[str, Any]:
    """A bus's remaining stops to the end of its route: scheduled, expected, and how far off each is.

    preds: the bus's current predictions [{stop, expected_at, direction, destination}]; route_stops: the route's
    stops in order for its direction [{stop, name, seq}]; timetable: the day's rows [{direction, journey, stop,
    scheduled_at}]."""
    if not preds:
        return {"vehicle": vehicle, "direction": None, "destination": None, "journey": None, "stops": []}
    direction = preds[0].get("direction")
    destination = next((p.get("destination") for p in preds if p.get("destination")), None)
    exp = {p["stop"]: p["expected_at"] for p in preds if p.get("expected_at")}
    sched = journeys(timetable, direction) if direction else {}
    j = which_journey(exp, sched)
    times = sched.get(j, {}) if j is not None else {}
    order = [s["stop"] for s in route_stops]
    known = [order.index(s) for s in exp if s in order]
    first = min(known) if known else None
    rows = []
    if first is not None:
        for s in route_stops[first:]:
            e, t = exp.get(s["stop"]), times.get(s["stop"])
            d = (e - t).total_seconds() if e and t else None
            rows.append({"seq": s.get("seq"), "stop": s["stop"], "name": s.get("name"), "scheduled_at": t, "expected_at": e,
                         "diff_s": round(d) if d is not None else None, "verdict": verdict(d), "words": words(d)})  # fmt: skip
    else:  # stops the route does not list (a diversion): the predictions alone, in time order
        for p in sorted(preds, key=lambda p: p["expected_at"] or datetime.max):
            t = times.get(p["stop"])
            d = (p["expected_at"] - t).total_seconds() if p.get("expected_at") and t else None
            rows.append({"seq": None, "stop": p["stop"], "name": p.get("stop_name"), "scheduled_at": t,
                         "expected_at": p.get("expected_at"), "diff_s": round(d) if d is not None else None,
                         "verdict": verdict(d), "words": words(d)})  # fmt: skip
    diffs = [r["diff_s"] for r in rows if r["diff_s"] is not None]
    now = diffs[0] if diffs else None
    return {"vehicle": vehicle, "direction": direction, "destination": destination, "journey": j,
            "departed": min(times.values()) if times else None, "now_diff_s": now, "now": words(now), "stops": rows}  # fmt: skip


def stop_hour(stop: str, at: datetime, timetable: list[dict[str, Any]], preds: list[dict[str, Any]],
              ends: dict[str, str], hour_s: int = 3600) -> dict[str, Any]:
    """A stop's next hour from `at`: what the timetable says, and what TfL expects (each bus once).

    ends: direction -> the name of the stop it ends at."""
    end = at + timedelta(seconds=hour_s)
    sched = sorted(({"scheduled_at": r["scheduled_at"], "direction": r["direction"], "journey": r["journey"],
                     "to": ends.get(r["direction"])} for r in timetable
                    if r["stop"] == stop and at - timedelta(minutes=2) <= r["scheduled_at"] < end),
                   key=lambda r: r["scheduled_at"])  # fmt: skip
    live = sorted(({"vehicle": p["vehicle"], "expected_at": p["expected_at"], "direction": p.get("direction"),
                    "to": p.get("destination") or ends.get(p.get("direction") or ""), "seen_at": p.get("seen_at")}
                   for p in preds if p["stop"] == stop and p.get("expected_at") and at - timedelta(minutes=2) <= p["expected_at"] < end),
                  key=lambda p: p["expected_at"])  # fmt: skip
    return {"stop": stop, "from": at, "to": end, "scheduled": sched, "expected": live}
