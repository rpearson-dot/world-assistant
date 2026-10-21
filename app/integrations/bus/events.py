"""Derived facts from tracked trips: leg times, and which buses made the target-stop run.

Pure functions over the plain-dict state from trips.py.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from datetime import tzinfo
from typing import Any

from app.integrations.bus.trips import all_trips

MATCH_TOLERANCE_MIN = 20  # a bus matches a timetable slot if it left within this of it
TERMINAL_MAX_GAP_S = 20 * 60  # the return-side stop only counts this soon after the stop before
RESOLVE_AFTER_MIN = 30  # a slot with no bus is a miss once this long after its scheduled time


def _by_sid(trip: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["sid"]: s for s in trip["stops"]}


def _confirmed(stops: dict[str, dict[str, Any]], sid: str, now: float) -> float | None:
    """A stop time only counts as confirmed once its predicted time has passed."""
    s = stops.get(sid)
    ts = s.get("expected") if s else None
    return ts if ts is not None and ts < now else None


def segment_observations(
    state: dict[str, Any], now: float, pairs: Sequence[tuple[str, str]]
) -> list[dict[str, Any]]:
    """Confirmed travel time between consecutive tracked stops, for every trip seen. Safe to
    call every poll: the caller stores them with a uniqueness key, so repeats are ignored."""
    out = []
    for trip in all_trips(state):
        stops = _by_sid(trip)
        for a, b in pairs:
            t0, t1 = _confirmed(stops, a, now), _confirmed(stops, b, now)
            if t0 is None or t1 is None:
                continue
            seconds = t1 - t0
            if 0 < seconds <= 3600:  # guard against contaminated data
                out.append({"vehicle": trip["vehicle"], "from": a, "to": b,
                            "observed_at": t1, "seconds": seconds})  # fmt: skip
    return out


def target_hits(
    state: dict[str, Any], now: float, cfg: dict[str, Any], earliest_ts: float, deadline_ts: float
) -> list[dict[str, Any]]:
    """Buses that left the boarding stop after `earliest_ts` and were CONFIRMED at the target
    stop before `deadline_ts`. Some buses terminate short of the target and stop on the
    return side; those count only if the same vehicle was at the stop before it just beforehand.
    Returns hits not yet reported today (the state remembers which)."""
    board, alight = cfg["board"], cfg["alight"]
    alight_return, before = cfg.get("alight_return"), cfg.get("before_alight")
    reported: set[str] = set(state.setdefault("reported", []))
    terminal: dict[str, Any] = state["terminal"]
    hits: list[dict[str, Any]] = []

    def qualifies(board_ts: float | None, arrive_ts: float | None) -> bool:
        return (
            board_ts is not None
            and arrive_ts is not None
            and arrive_ts < now
            and board_ts >= earliest_ts
            and board_ts < arrive_ts < deadline_ts
        )

    trips = all_trips(state)
    for trip in trips:
        # No check on the trip's direction label: the stops are specific to one side of the road, so a bus
        # seen at the boarding stop is going the right way. (A stop that TfL lists in both directions made the
        # label disagree with the one chosen at set-up, and every bus was ignored.)
        vehicle, stops = trip["vehicle"], _by_sid(trip)
        board_ts = (stops.get(board) or {}).get("expected")
        if board_ts is None:
            continue
        if before and (stops.get(before) or {}).get("expected") is not None:
            terminal[vehicle] = {"board_ts": board_ts, "before_ts": stops[before]["expected"], "dir": trip["dir"]}
        elif cfg.get("either_side") and alight_return:  # no need to have been seen at the stop before
            seen = [x["expected"] for x in trip["stops"] if x.get("expected") is not None]
            if seen:
                terminal[vehicle] = {"board_ts": board_ts, "before_ts": max(seen), "dir": trip["dir"]}
        key = f"{vehicle}@{int(board_ts)}"
        if key in reported:
            continue
        arrive_ts = (stops.get(alight) or {}).get("expected")
        if qualifies(board_ts, arrive_ts):
            hits.append({"vehicle": vehicle, "board_ts": board_ts, "arrive_ts": arrive_ts,
                         "via_return": False})  # fmt: skip
            reported.add(key)
            terminal.pop(vehicle, None)

    if alight_return:
        for trip in trips:
            vehicle = trip["vehicle"]
            cand = terminal.get(vehicle)
            return_ts = (_by_sid(trip).get(alight_return) or {}).get("expected")
            if cand is None or return_ts is None:
                continue
            if trip["dir"] == cand.get("dir", cfg.get("direction", "outbound")):
                continue  # still the same run: it has to have turned round to be on the other side
            key = f"{vehicle}@{int(cand['board_ts'])}"
            gap = return_ts - cand["before_ts"]
            if key in reported or gap < 0 or gap > TERMINAL_MAX_GAP_S:
                continue
            if qualifies(cand["board_ts"], return_ts):
                hits.append({"vehicle": vehicle, "board_ts": cand["board_ts"],
                             "arrive_ts": return_ts, "via_return": True})  # fmt: skip
                reported.add(key)
                del terminal[vehicle]

    for vehicle, cand in list(terminal.items()):  # too old to ever match
        if now - cand["before_ts"] > 3600:
            del terminal[vehicle]
    state["reported"] = sorted(reported)
    return hits


def to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def nearest_slot(slots: Sequence[str], hhmm: str, taken: Collection[str] = ()) -> str | None:
    """The scheduled slot closest to `hhmm` (within the tolerance), skipping any in `taken`."""
    best, best_diff = None, None
    target = to_minutes(hhmm)
    for s in slots:
        if s in taken:
            continue
        diff = abs(to_minutes(s) - target)
        if best_diff is None or diff < best_diff:
            best, best_diff = s, diff
    return best if best_diff is not None and best_diff <= MATCH_TOLERANCE_MIN else None


def assign_slots(boards: Sequence[int], slots: Sequence[str], taken: Collection[str] = ()) -> list[str | None]:
    """Match buses to scheduled departures in order. `boards` are the minutes-of-day each bus left the
    boarding stop, earliest first; buses on one route do not overtake, so the n-th bus gets a later slot
    than the (n-1)-th. Picks the most matches first (a bus is unmatched only if no free slot is within
    the tolerance and the order allows), then the smallest total gap. Slots in `taken` are not offered."""
    free = sorted((s for s in slots if s not in taken), key=to_minutes)
    n, m, unmatched = len(boards), len(free), 10_000
    inf = float("inf")
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]  # dp[i][j]: best cost for the first i buses and first j slots
    for i in range(1, n + 1):
        dp[i][0] = i * unmatched
        for j in range(1, m + 1):
            gap = abs(to_minutes(free[j - 1]) - boards[i - 1])
            pair = dp[i - 1][j - 1] + gap if gap <= MATCH_TOLERANCE_MIN else inf
            dp[i][j] = min(dp[i][j - 1], dp[i - 1][j] + unmatched, pair)
    out: list[str | None] = [None] * n
    i, j = n, m
    while i > 0 and j > 0:
        gap = abs(to_minutes(free[j - 1]) - boards[i - 1])
        if gap <= MATCH_TOLERANCE_MIN and dp[i][j] == dp[i - 1][j - 1] + gap:
            out[i - 1] = free[j - 1]
            i, j = i - 1, j - 1
        elif dp[i][j] == dp[i][j - 1]:
            j -= 1
        else:
            i -= 1
    return out


def slots_in_window(
    times: Sequence[str], earliest: str, deadline: str, min_travel_min: int
) -> list[str]:
    """Scheduled departures worth tracking: from `earliest`, and early enough that the
    journey could still end before the deadline."""
    lo, hi = to_minutes(earliest), to_minutes(deadline) - min_travel_min
    return [t for t in times if lo <= to_minutes(t) <= hi]


def slot_reliability(
    days: Sequence[dict[str, Any]], today: str, now_minutes: int
) -> list[dict[str, Any]]:
    """Per scheduled slot over the stored days: how often a bus actually made it. Slots on
    today's unfinished record count only once enough time has passed to be a real miss."""
    stats: dict[str, dict[str, int]] = {}
    for day in days:
        hit_slots = set(day.get("hit_slots", []))
        for slot in day.get("slots", []):
            if (
                day["date"] == today
                and slot not in hit_slots
                and now_minutes <= to_minutes(slot) + RESOLVE_AFTER_MIN
            ):
                continue  # still pending today: not a miss yet
            s = stats.setdefault(slot, {"scheduled": 0, "hit": 0})
            s["scheduled"] += 1
            s["hit"] += slot in hit_slots
    return [
        {"slot": slot, "days_scheduled": s["scheduled"], "days_hit": s["hit"],
         "miss_rate": round(1 - s["hit"] / s["scheduled"], 2) if s["scheduled"] else None}
        for slot, s in sorted(stats.items())
    ]  # fmt: skip


def condition_summary(
    obs: Sequence[dict[str, Any]], now: float, recent_s: float = 1800
) -> dict[str, Any]:
    """Compare the last 30 minutes with the day's average for one leg."""
    day = [o["seconds"] for o in obs]
    recent = [o["seconds"] for o in obs if o["observed_at"] >= now - recent_s]
    day_avg = sum(day) / len(day) if day else None
    recent_avg = sum(recent) / len(recent) if recent else None
    delta = (
        round((recent_avg - day_avg) / 60, 1)
        if day_avg is not None and recent_avg is not None
        else None
    )
    return {"day_avg_min": round(day_avg / 60, 1) if day_avg is not None else None,
            "recent_avg_min": round(recent_avg / 60, 1) if recent_avg is not None else None,
            "delta_min": delta, "day_samples": len(day), "recent_samples": len(recent)}  # fmt: skip


def turnarounds(
    state: dict[str, Any], place_of: Callable[[str], str | None]
) -> list[dict[str, Any]]:
    """A bus that turns back short of the end of the route shows up as two stops at the same
    place in a row, one in each direction (e.g. outbound then inbound at the same road). Report
    each once, with both times and the gap between them, once the second stop has been reached."""
    done: set[str] = set(state.setdefault("turned", []))
    out: list[dict[str, Any]] = []
    for vehicle, trip in state["trips"].items():
        if not trip["stops"]:
            continue
        first = trip["stops"][0]
        prev_trip = next(
            (t for t in reversed(state["recent"]) if t["vehicle"] == vehicle and t["stops"]), None
        )
        if prev_trip is None or prev_trip["dir"] == trip["dir"]:
            continue
        last = prev_trip["stops"][-1]
        place = place_of(last["sid"])
        if place is None or place != place_of(first["sid"]):
            continue
        a, b = last.get("expected"), first.get("expected")
        if a is None or b is None or b < a:
            continue
        key = f"{vehicle}@{int(a)}"
        if key in done:
            continue
        done.add(key)
        out.append({"vehicle": vehicle, "type": "turnaround", "place": place,
                    "from_sid": last["sid"], "to_sid": first["sid"],
                    "from_dir": prev_trip["dir"], "to_dir": trip["dir"],
                    "from_at": a, "to_at": b, "gap_s": round(b - a)})  # fmt: skip
    state["turned"] = sorted(done)[-200:]
    return out


def sentence(last: dict[str, Any] | None, deadline: str, tz: tzinfo) -> str:
    if not last:
        return f"No bus has been confirmed at the target stop before {deadline} yet."
    when = last["arrive_at"].astimezone(tz).strftime("%H:%M")
    delay = last.get("delay_min")
    extra = f", about {abs(delay):.0f} min {'late' if delay > 0 else 'early'}" if delay else ""
    return f"The last bus before {deadline} reached the target stop at {when}{extra}."


def outlook(
    *, slots: Sequence[str], hit_slots: Sequence[str], reliability: Sequence[dict[str, Any]],
    now_minutes: int, show_until: str, weekday_ok: bool, min_days: int = 3,
) -> dict[str, Any]:  # fmt: skip
    """The chance each remaining scheduled bus gets there by the deadline, from how often that
    slot has made it before. Not relevant (and no numbers) at weekends or after `show_until`.
    A slot with fewer than `min_days` recorded days borrows the rate across all slots."""
    rel = {r["slot"]: r for r in reliability}
    total = sum(r["days_scheduled"] for r in reliability)
    pooled = sum(r["days_hit"] for r in reliability) / total if total else None
    out: dict[str, Any] = {"relevant": False, "reason": None, "slots": [], "next_slot": None,
                           "chance": None, "days": 0, "basis": None, "pooled_chance": None if pooled is None else round(pooled * 100),
                           "days_recorded": max((r["days_scheduled"] for r in reliability), default=0)}  # fmt: skip
    if not weekday_ok:
        out["reason"] = "weekend"
        return out
    if now_minutes > to_minutes(show_until):
        out["reason"] = "after " + show_until
        return out
    for slot in slots:
        if to_minutes(slot) < now_minutes - 1 or slot in hit_slots:
            continue
        r = rel.get(slot)
        if r and r["days_scheduled"] >= min_days:
            p, n, basis = r["days_hit"] / r["days_scheduled"], r["days_scheduled"], "this departure"
        elif pooled is not None:
            p, n, basis = pooled, total, "all departures"
        else:
            p, n, basis = None, 0, None
        out["slots"].append({"slot": slot, "chance": None if p is None else round(p * 100),
                             "days": n, "basis": basis})  # fmt: skip
    if not out["slots"]:
        out["reason"] = "no more buses that could make it"
        return out
    first = out["slots"][0]
    out.update(relevant=True, next_slot=first["slot"], chance=first["chance"], days=first["days"], basis=first["basis"])
    return out


def result_log(
    day_slots: Sequence[tuple[str, Sequence[str]]], hits: Sequence[dict[str, Any]], today: str, now_minutes: int
) -> dict[str, Any]:  # fmt: skip
    """Success and failure for every scheduled bus, newest day first. `day_slots` is
    (date, slots); `hits` carry date, slot, arrive (local HH:MM) and delay_min. A slot with no bus
    is a failure once RESOLVE_AFTER_MIN has passed; until then it is pending."""
    by = {(h["date"], h["slot"]): h for h in hits if h.get("slot")}
    days: list[dict[str, Any]] = []
    ok = bad = 0
    for date, slots in sorted(day_slots, reverse=True):
        rows = []
        for slot in slots:
            h = by.get((date, slot))
            if h:
                rows.append({"slot": slot, "result": "success", "arrived": h.get("arrive"), "delay_min": h.get("delay_min")})
                ok += 1
            elif date == today and now_minutes <= to_minutes(slot) + RESOLVE_AFTER_MIN:
                rows.append({"slot": slot, "result": "pending"})
            else:
                rows.append({"slot": slot, "result": "failure"})
                bad += 1
        days.append({"date": date, "buses": rows})
    return {"success": ok, "failure": bad, "success_rate": round(100 * ok / (ok + bad)) if ok + bad else None, "days": days}


EARLY_MARGIN = 2  # a bus last seen at least this many stops short of the end of its route ended early


def early_terminations(
    state: dict[str, Any], end_pos: dict[str, int], names: dict[str, str], margin: int = EARLY_MARGIN
) -> list[dict[str, Any]]:  # fmt: skip
    """Buses that ended a trip short of the end of the route, each reported once: where they were last
    seen and when, and whether they turned back (and where they next appeared) or just stopped
    appearing in the feed. Drains the state's list of trips that have ended."""
    ended, state["ended"] = list(state.get("ended") or []), []
    out: list[dict[str, Any]] = []
    for e in ended:
        end = end_pos.get(e["dir"])
        if e["reason"] == "lap" or end is None or end - e["pos"] < margin:
            continue
        nxt = e.get("next") or {}
        out.append({
            "vehicle": e["vehicle"], "type": "early_termination", "sid": e["sid"], "stop": names.get(e["sid"]) or None,
            "direction": e["dir"], "at": e.get("at"), "stops_before_end": end - e["pos"],
            "kind": "turned back" if e["reason"] == "reversed" else "stopped reporting",
            "resumed_sid": nxt.get("sid"), "resumed_stop": names.get(nxt.get("sid") or "") or None, "resumed_at": nxt.get("at"),
        })  # fmt: skip
    return out
