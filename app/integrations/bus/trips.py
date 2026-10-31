"""Follow each physical bus through the stops of a route.

Ported from the Home Assistant pyscript tracker. TfL's Arrivals feed returns roughly one
row per UPCOMING STOP within its prediction horizon, not one row per bus, so only each
vehicle's soonest prediction is used as its position. A stop counts as reached once the
bus's soonest prediction has moved on to a later stop, or once its prediction was under
PASSED_ETA seconds away and then vanished.

All functions are pure: they take and mutate a plain-dict `state` (JSON-serialisable, so it
can be stored between polls) and never touch the network or the database.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

STALE_SECONDS = 300  # silent this long and the trip is finished
MAX_GAP_FOR_END = 600  # a trip silent for longer than this (overnight, say) did not 'end' at a place: no log entry
PASSED_ETA = 120  # seconds: a vanished prediction this close counts as passed
MAX_RECENT = 40

PosOf = Callable[[str], tuple[str, int] | None]  # stop id -> (direction, position)


def new_state() -> dict[str, Any]:
    return {"trips": {}, "recent": [], "evaluated": {}, "terminal": {}, "day": None, "ended": []}


def epoch(iso: Any) -> float | None:
    """TfL expectedArrival ('2026-09-21T08:00:00Z') to epoch seconds."""
    if not isinstance(iso, str) or not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


def soonest_by_vehicle(preds: list[dict[str, Any]], pos_of: PosOf) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for p in preds:
        vehicle, sid, eta = p.get("vehicleId"), p.get("naptanId"), p.get("timeToStation")
        if not vehicle or not sid or eta is None:
            continue
        where = pos_of(sid)
        if where is None:
            continue
        cur = out.get(vehicle)
        if cur is None or eta < cur["eta"]:
            out[vehicle] = {"sid": sid, "dir": where[0], "pos": where[1], "eta": eta,
                            "expected": epoch(p.get("expectedArrival"))}  # fmt: skip
    return out


def _record(trip: dict[str, Any], pending: dict[str, Any] | None) -> None:
    if pending is None:
        return
    stops = trip["stops"]
    if not stops or stops[-1]["sid"] != pending["sid"]:
        stops.append(
            {"sid": pending["sid"], "pos": pending["pos"], "expected": pending["expected"]}
        )


def _retire(state: dict[str, Any], trip: dict[str, Any]) -> None:
    state["recent"].append(trip)
    del state["recent"][:-MAX_RECENT]


def _end(state: dict[str, Any], trip: dict[str, Any], reason: str, resumed: dict[str, Any] | None) -> None:
    """Remember how a trip ended and where it was last seen, for events.early_terminations.
    reason: "reversed" (the same bus next appears going the other way), "lap" (back at the start),
    "gone" (it stopped appearing in the feed)."""
    if not trip["stops"]:
        return
    last = trip["stops"][-1]
    ended = state.setdefault("ended", [])
    ended.append({"vehicle": trip["vehicle"], "dir": trip["dir"], "sid": last["sid"], "pos": last["pos"],
                  "at": last.get("expected"), "reason": reason,
                  "next": {"sid": resumed["sid"], "at": resumed.get("expected")} if resumed else None})  # fmt: skip
    del ended[:-100]


def update_trips(
    state: dict[str, Any], preds: list[dict[str, Any]], pos_of: PosOf, now: float
) -> list[dict[str, Any]]:
    """Fold one poll's predictions into the state. Returns trips that finished this poll."""
    seen = soonest_by_vehicle(preds, pos_of)
    trips: dict[str, dict[str, Any]] = state["trips"]

    for vehicle, info in seen.items():
        trip = trips.get(vehicle)
        new_trip = False
        if trip is not None:
            if info["dir"] != trip["dir"]:
                new_trip = True  # turned round
            elif info["pos"] == 0 and trip["stops"] and trip["stops"][-1]["pos"] != 0:
                new_trip = True  # back at the origin having made progress: next lap
        if trip is None or new_trip:
            if trip is not None:
                _record(trip, trip.get("pending"))
                if now - trip["last_seen"] <= MAX_GAP_FOR_END:
                    _end(state, trip, "reversed" if info["dir"] != trip["dir"] else "lap", info)
                _retire(state, trip)
            trip = {"vehicle": vehicle, "dir": info["dir"], "last_pos": info["pos"],
                    "last_seen": now, "stops": [], "pending": None}  # fmt: skip
            trips[vehicle] = trip
            state["evaluated"].pop(vehicle, None)
            state.setdefault("firsts", []).append(  # where along the route the feed first showed this run
                {"vehicle": vehicle, "dir": info["dir"], "sid": info["sid"], "pos": info["pos"],
                 "eta": info["eta"], "at": now})  # fmt: skip
        trip.update(dir=info["dir"], last_pos=info["pos"], last_seen=now)
        pending = trip.get("pending")
        current = {
            "sid": info["sid"],
            "pos": info["pos"],
            "eta": info["eta"],
            "expected": info["expected"],
        }
        if pending is None:
            trip["pending"] = current
        elif pending["sid"] == info["sid"]:
            pending.update(eta=info["eta"], expected=info["expected"])
        else:
            _record(trip, pending)  # the soonest stop moved on: the old one has been passed
            trip["pending"] = current

    finished = []
    for vehicle, trip in list(trips.items()):
        if vehicle in seen:
            continue
        pending = trip.get("pending")
        if pending is not None and (pending.get("eta") or 999) < PASSED_ETA:
            _record(trip, pending)
            trip["pending"] = None
        if now - trip["last_seen"] > STALE_SECONDS:
            finished.append(trip)
            del trips[vehicle]
            state["evaluated"].pop(vehicle, None)
    for trip in finished:
        if now - trip["last_seen"] <= MAX_GAP_FOR_END:
            _end(state, trip, "gone", None)
        _retire(state, trip)
    return finished


def first_sightings(state: dict[str, Any], names: dict[str, str]) -> list[dict[str, Any]]:
    """Where and when each new run first appeared in the feed (type 'first_seen'), reported once. Kept for
    working out how far ahead the feed shows a bus and the order buses left the terminus; not shown to users."""
    firsts, state["firsts"] = state.get("firsts") or [], []
    return [{"vehicle": f["vehicle"], "type": "first_seen", "dir": f["dir"], "sid": f["sid"], "stop": names.get(f["sid"]),
             "pos": f["pos"], "eta_s": f["eta"], "seen_at": f["at"]} for f in firsts]  # fmt: skip


def all_trips(state: dict[str, Any]) -> list[dict[str, Any]]:
    return list(state["trips"].values()) + list(state["recent"])


def detect_anomalies(
    state: dict[str, Any], dir_of: Callable[[str], str | None],
    names: dict[str, str] | None = None, towards: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Trip starts and direction changes, each reported once, at the moment a new stop is added to a trip. A bus
    reported at an earlier stop than before in the same direction is not reported: that is the live predictions
    shifting, not the bus. With `names` and `towards` each says where (the stop's name) and which
    way (where that direction is heading: "towards Kingston")."""
    names, towards = names or {}, towards or {}

    def where(sid: str, direction: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if names.get(sid):
            out["stop"] = names[sid]
        if direction and towards.get(direction):
            out["towards"] = towards[direction]
        return out

    out: list[dict[str, Any]] = []
    for vehicle, trip in state["trips"].items():
        stops = trip["stops"]
        seen = state["evaluated"].get(vehicle, 0)
        if len(stops) < seen:
            seen = 0
        if len(stops) <= seen:
            continue
        state["evaluated"][vehicle] = len(stops)
        latest = stops[-1]
        latest_dir = dir_of(latest["sid"])
        if len(stops) == 1:
            if latest["pos"] == 0:
                out.append({"vehicle": vehicle, "type": "trip_start", "sid": latest["sid"],
                            **where(latest["sid"], latest_dir)})  # fmt: skip
            continue
        prev = stops[-2]
        prev_dir = dir_of(prev["sid"])
        if latest_dir != prev_dir:
            out.append({"vehicle": vehicle, "type": "direction_change", "sid": latest["sid"],
                        **where(latest["sid"], latest_dir),
                        "from_towards": towards.get(prev_dir or ""), "from_stop": names.get(prev["sid"])})  # fmt: skip
        elif latest["pos"] == 0 and prev["pos"] != 0:
            out.append({"vehicle": vehicle, "type": "trip_start", "sid": latest["sid"],
                        **where(latest["sid"], latest_dir)})  # fmt: skip
    return out
