from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.integrations.bus import events, trips

OUT = ["s0", "s1", "s2", "s3", "s4", "s5"]  # outbound: s2 = boarding stop, s4 = target stop
IN = ["i0", "i1", "i2"]  # inbound; i1 is the return-side stop by the target
POS = {sid: ("outbound", i) for i, sid in enumerate(OUT)} | {
    sid: ("inbound", i) for i, sid in enumerate(IN)
}
CFG = {"board": "s2", "alight": "s4", "alight_return": "i1", "before_alight": "s3"}
T0 = datetime(2026, 10, 20, 6, 0, tzinfo=UTC).timestamp()  # arbitrary base time


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


def pred(vehicle: str, sid: str, expected: float, now: float) -> dict[str, Any]:
    return {"vehicleId": vehicle, "naptanId": sid, "timeToStation": max(int(expected - now), 0),
            "expectedArrival": iso(expected)}  # fmt: skip


def run_bus(state: dict[str, Any], vehicle: str, arrivals: dict[str, float], start: float, end: float,
            step: float = 30.0, anoms: list[dict[str, Any]] | None = None) -> None:  # fmt: skip
    """Simulate polls: each poll the bus reports a prediction for every stop it has yet to reach
    (TfL's behaviour: one row per upcoming stop), the same way the real feed does."""
    now = start
    while now <= end:
        rows = [pred(vehicle, sid, t, now) for sid, t in arrivals.items() if t > now - 5]
        trips.update_trips(state, rows, POS.get, now)
        if anoms is not None:
            anoms += trips.detect_anomalies(state, lambda sid: POS[sid][0])
        now += step


def test_soonest_prediction_only_and_stops_reached() -> None:
    state = trips.new_state()
    arrivals = {"s0": T0 + 60, "s1": T0 + 300, "s2": T0 + 600, "s3": T0 + 900, "s4": T0 + 1200}
    anoms: list[dict[str, Any]] = []
    run_bus(state, "V1", arrivals, T0, T0 + 1300, anoms=anoms)
    trip = state["trips"]["V1"]
    assert [s["sid"] for s in trip["stops"]] == [
        "s0",
        "s1",
        "s2",
        "s3",
    ]  # s4 still pending/vanishing
    assert trip["stops"][0]["pos"] == 0
    # one bus, many rows per poll, but only one trip and no spurious reverse moves
    assert len(state["trips"]) == 1
    assert [a["type"] for a in anoms] == ["trip_start"]  # evaluated once, no reverse movements


def test_vanished_close_prediction_counts_and_stale_trip_finishes() -> None:
    state = trips.new_state()
    run_bus(state, "V1", {"s3": T0 + 100, "s4": T0 + 400}, T0, T0 + 400)
    # bus disappears from the feed; the last prediction was < 120 s away
    for k in range(1, 4):
        trips.update_trips(state, [], POS.get, T0 + 400 + k * 30)
    assert [s["sid"] for s in state["trips"]["V1"]["stops"]][-1] == "s4"
    done = trips.update_trips(state, [], POS.get, T0 + 400 + 400)
    assert [t["vehicle"] for t in done] == ["V1"] and "V1" not in state["trips"]
    assert state["recent"][-1]["vehicle"] == "V1"


def test_direction_change_starts_a_new_trip() -> None:
    state = trips.new_state()
    run_bus(state, "V1", {"s4": T0 + 100, "s5": T0 + 200}, T0, T0 + 150)
    run_bus(state, "V1", {"i1": T0 + 400, "i2": T0 + 500}, T0 + 330, T0 + 420)
    assert state["trips"]["V1"]["dir"] == "inbound"
    assert state["recent"][-1]["dir"] == "outbound"


def test_unknown_stops_are_ignored() -> None:
    state = trips.new_state()
    trips.update_trips(state, [pred("V9", "zzz", T0 + 60, T0)], POS.get, T0)
    assert state["trips"] == {}


def test_segment_observations_only_when_confirmed() -> None:
    state = trips.new_state()
    run_bus(
        state,
        "V1",
        {"s1": T0 + 100, "s2": T0 + 400, "s3": T0 + 700, "s4": T0 + 1000},
        T0,
        T0 + 1100,
    )
    pairs = [("s1", "s2"), ("s2", "s3"), ("s3", "s4")]
    obs = events.segment_observations(state, T0 + 800, pairs)
    assert [(o["from"], o["to"], o["seconds"]) for o in obs] == [
        ("s1", "s2", 300),
        ("s2", "s3", 300),
    ]
    # s4's time is still in the future at T0+800, so the last leg isn't confirmed yet
    assert len(events.segment_observations(state, T0 + 1500, pairs)) == 3


def test_target_hit_before_deadline_and_not_after() -> None:
    state = trips.new_state()
    run_bus(state, "V1", {"s2": T0 + 600, "s3": T0 + 1000, "s4": T0 + 1300}, T0, T0 + 1400)
    now = T0 + 1500
    hits = events.target_hits(state, now, CFG, earliest_ts=T0, deadline_ts=T0 + 1400)
    assert [(h["vehicle"], h["via_return"]) for h in hits] == [("V1", False)]
    assert events.target_hits(state, now, CFG, T0, T0 + 1400) == []  # reported once
    late = trips.new_state()
    run_bus(late, "V2", {"s2": T0 + 600, "s3": T0 + 1000, "s4": T0 + 1300}, T0, T0 + 1400)
    assert events.target_hits(late, now, CFG, T0, T0 + 1200) == []  # arrived after the deadline
    early = trips.new_state()
    run_bus(early, "V3", {"s2": T0 + 600, "s3": T0 + 1000, "s4": T0 + 1300}, T0, T0 + 1400)
    assert (
        events.target_hits(early, now, CFG, T0 + 700, T0 + 1400) == []
    )  # left before the earliest time


def test_bus_that_terminates_short_counts_via_return_stop() -> None:
    state = trips.new_state()
    run_bus(state, "V1", {"s2": T0 + 600, "s3": T0 + 900}, T0, T0 + 1000)
    run_bus(state, "V1", {"i1": T0 + 1100, "i2": T0 + 1500}, T0 + 1000, T0 + 1200)
    hits = events.target_hits(state, T0 + 1300, CFG, T0, T0 + 1400)
    assert [(h["vehicle"], h["via_return"]) for h in hits] == [("V1", True)]


def test_assign_slots_keeps_buses_in_order() -> None:
    slots = ["07:14", "07:26", "07:38", "07:49"]
    m = events.to_minutes
    # today's real case: 07:14, then two bunched buses just after 07:26
    assert events.assign_slots([m("07:14"), m("07:27"), m("07:28")], slots) == ["07:14", "07:26", "07:38"]
    # a late bus cannot jump ahead of the one in front of it
    assert events.assign_slots([m("07:30"), m("07:31")], slots) == ["07:26", "07:38"]
    # one bus between two slots takes the nearer; a slot already taken is not offered
    assert events.assign_slots([m("07:20")], slots) == ["07:26"]
    assert events.assign_slots([m("07:20")], slots, taken={"07:26"}) == ["07:14"]
    # nothing within reach: no slot, and the others still match
    assert events.assign_slots([m("07:14"), m("09:30")], slots) == ["07:14", None]
    assert events.assign_slots([], slots) == []


def test_slots_matching_and_reliability() -> None:
    times = ["06:50", "07:11", "07:23", "07:46", "08:00", "08:10"]
    # with a 08:10 deadline and a 12 minute minimum journey, only departures up to 07:58 can make it
    slots = events.slots_in_window(times, "07:00", "08:10", 12)
    assert slots == ["07:11", "07:23", "07:46"]
    assert events.nearest_slot(slots, "07:20") == "07:23"
    assert events.nearest_slot(slots, "09:30") is None
    assert events.nearest_slot(slots, "07:20", taken={"07:23"}) == "07:11"
    assert events.nearest_slot(slots, "07:20", taken=set(slots)) is None
    days = [
        {"date": "2026-10-19", "slots": slots, "hit_slots": ["07:11", "07:23"]},
        {"date": "2026-10-20", "slots": slots, "hit_slots": ["07:11"]},
    ]
    # 07:30 today: 07:23 isn't a miss yet (needs 30 min), 07:46 certainly isn't
    rel = {r["slot"]: r for r in events.slot_reliability(days, "2026-10-20", 7 * 60 + 30)}
    assert rel["07:23"]["days_scheduled"] == 1 and rel["07:23"]["miss_rate"] == 0.0
    assert rel["07:46"]["days_scheduled"] == 1 and rel["07:46"]["miss_rate"] == 1.0
    rel = {r["slot"]: r for r in events.slot_reliability(days, "2026-10-20", 8 * 60)}
    assert rel["07:23"]["days_scheduled"] == 2 and rel["07:23"]["miss_rate"] == 0.5


def test_condition_summary() -> None:
    now = 10_000.0
    obs = [{"observed_at": now - 7200, "seconds": 600}, {"observed_at": now - 600, "seconds": 480}]
    c = events.condition_summary(obs, now)
    assert c["day_avg_min"] == 9.0 and c["recent_avg_min"] == 8.0 and c["delta_min"] == -1.0
    assert events.condition_summary([], now)["delta_min"] is None
    assert events.to_minutes("08:10") == 490
    with pytest.raises(ValueError):
        events.to_minutes("bad")


def test_either_side_of_the_road_counts_without_being_seen_at_the_stop_before() -> None:
    def run(cfg: dict[str, Any]) -> list[dict[str, Any]]:
        state = trips.new_state()
        run_bus(state, "V1", {"s2": T0 + 600, "s3": T0 + 900}, T0, T0 + 1000)
        run_bus(state, "V1", {"i1": T0 + 1100, "i2": T0 + 1500}, T0 + 1000, T0 + 1200)
        return events.target_hits(state, T0 + 1300, cfg, T0, T0 + 1400)

    no_before = {"board": "s2", "alight": "s4", "alight_return": "i1", "before_alight": None}
    assert run({**no_before, "either_side": True})[0]["via_return"] is True
    assert run({**no_before, "either_side": False}) == []  # the strict rule still needs the stop before
    far = {**no_before, "either_side": True}
    state = trips.new_state()
    run_bus(state, "V1", {"s2": T0 + 600}, T0, T0 + 700)  # last seen long before it appears on the other side
    run_bus(state, "V1", {"i1": T0 + 3000}, T0 + 2900, T0 + 3000)
    assert events.target_hits(state, T0 + 3100, far, T0, T0 + 4000) == []


def test_chance_comes_from_how_often_each_departure_made_it_and_is_hidden_after_0800() -> None:
    rel = [
        {"slot": "07:23", "days_scheduled": 10, "days_hit": 9, "miss_rate": 0.1},
        {"slot": "07:46", "days_scheduled": 10, "days_hit": 5, "miss_rate": 0.5},
        {"slot": "07:55", "days_scheduled": 1, "days_hit": 0, "miss_rate": 1.0},
    ]
    kw: dict[str, Any] = {"slots": ["07:23", "07:46", "07:55"], "hit_slots": [], "reliability": rel,
                          "show_until": "08:00", "weekday_ok": True}  # fmt: skip
    o = events.outlook(now_minutes=7 * 60 + 20, **kw)
    assert o["relevant"] and o["next_slot"] == "07:23" and o["chance"] == 90 and o["basis"] == "this departure"
    assert [x["chance"] for x in o["slots"]] == [90, 50, 67]  # 07:55 has one day: borrows the overall 14 of 21
    assert o["slots"][2]["basis"] == "all departures"
    later = events.outlook(now_minutes=7 * 60 + 30, **kw)
    assert later["next_slot"] == "07:46" and later["chance"] == 50  # 07:23 has gone
    assert events.outlook(now_minutes=8 * 60 + 1, **kw) == {**events.outlook(now_minutes=8 * 60 + 1, **kw), "relevant": False, "chance": None, "slots": []}
    assert events.outlook(now_minutes=8 * 60 + 1, **kw)["reason"] == "after 08:00"
    assert events.outlook(now_minutes=7 * 60, **{**kw, "weekday_ok": False})["reason"] == "weekend"
    done = events.outlook(now_minutes=7 * 60, **{**kw, "hit_slots": ["07:23"]})
    assert done["next_slot"] == "07:46"  # a departure that already made it is not "next"
    none = events.outlook(now_minutes=7 * 60, **{**kw, "reliability": []})
    assert none["relevant"] and none["chance"] is None  # no history yet: no number


def test_result_log_records_success_failure_and_pending() -> None:
    days = [("2026-10-20", ["07:11", "07:46"]), ("2026-10-21", ["07:11", "07:46", "07:55"])]
    hits = [{"date": "2026-10-20", "slot": "07:11", "arrive": "07:25", "delay_min": 1.0},
            {"date": "2026-10-21", "slot": "07:46", "arrive": "08:02", "delay_min": None}]  # fmt: skip
    out = events.result_log(days, hits, "2026-10-21", 7 * 60 + 50)
    assert [d["date"] for d in out["days"]] == ["2026-10-21", "2026-10-20"]  # newest first
    today = {b["slot"]: b["result"] for b in out["days"][0]["buses"]}
    assert today == {"07:11": "failure", "07:46": "success", "07:55": "pending"}  # 07:55 is under 30 min old
    yesterday = {b["slot"]: b["result"] for b in out["days"][1]["buses"]}
    assert yesterday == {"07:11": "success", "07:46": "failure"}
    assert (out["success"], out["failure"], out["success_rate"]) == (2, 2, 50)
    assert events.result_log([], [], "2026-10-21", 0)["success_rate"] is None


def test_tracker_config_defaults_to_either_side_and_a_0800_cutoff() -> None:
    from app.integrations.bus.config import TrackerConfig

    cfg = TrackerConfig.model_validate({
        "line": "213",
        "stops": [{"key": "a", "sid": "490000001A", "label": "A"}, {"key": "b", "sid": "490000002B", "label": "B"}],
        "target": {"board": "a", "alight": "b"},
    })  # fmt: skip
    assert cfg.show_until == "08:00" and cfg.target is not None and cfg.target.either_side is True
    assert cfg.target_cfg()["either_side"] is True  # type: ignore[index]


END = {"outbound": 5, "inbound": 2}  # the last position of each direction in this test route
NAMES = {"s3": "Third Stop", "s4": "Fourth Stop", "i0": "Back Start", "i1": "Back One"}


def test_a_bus_that_turns_back_short_of_the_end_is_logged_with_where_and_when() -> None:
    state = trips.new_state()
    run_bus(state, "V1", {"s2": T0 + 600, "s3": T0 + 900}, T0, T0 + 1000)  # outbound, last seen at s3 (2 stops short)
    run_bus(state, "V1", {"i0": T0 + 1300, "i1": T0 + 1500}, T0 + 1100, T0 + 1250)  # next seen going the other way
    found = events.early_terminations(state, END, NAMES)
    assert len(found) == 1
    e = found[0]
    assert e["type"] == "early_termination" and e["kind"] == "turned back" and e["vehicle"] == "V1"
    assert e["stop"] == "Third Stop" and e["stops_before_end"] == 2 and e["direction"] == "outbound"
    assert e["resumed_stop"] == "Back Start" and e["resumed_at"] is not None and e["at"] is not None
    assert events.early_terminations(state, END, NAMES) == []  # reported once


def test_a_bus_that_stops_reporting_mid_route_is_logged_but_one_finishing_normally_is_not() -> None:
    state = trips.new_state()
    run_bus(state, "V1", {"s2": T0 + 600, "s3": T0 + 900}, T0, T0 + 1000)
    run_bus(state, "V2", {"s3": T0 + 900, "s4": T0 + 1200, "s5": T0 + 1500}, T0 + 600, T0 + 1600)  # ran to the end
    trips.update_trips(state, [], POS.get, T0 + 2500)  # both silent for longer than STALE_SECONDS
    found = events.early_terminations(state, END, NAMES)
    assert [(e["vehicle"], e["kind"]) for e in found] == [("V1", "stopped reporting")]
    assert found[0]["resumed_stop"] is None


def test_starting_the_next_lap_is_not_an_early_end() -> None:
    state = trips.new_state()
    run_bus(state, "V1", {"s0": T0 + 60, "s1": T0 + 300, "s2": T0 + 600}, T0, T0 + 700)
    run_bus(state, "V1", {"s0": T0 + 2000, "s1": T0 + 2300}, T0 + 1900, T0 + 2100)  # back at the origin: a new lap
    assert events.early_terminations(state, END, NAMES) == []


def test_the_direction_label_does_not_decide_whether_a_bus_counts() -> None:
    """Real data: TfL labelled the trips that run board -> target 'inbound' while set-up had stored
    'outbound'; every bus was ignored. The stops are side-specific, so the label must not matter."""
    state = trips.new_state()
    run_bus(state, "V1", {"s2": T0 + 600, "s3": T0 + 1000, "s4": T0 + 1300}, T0, T0 + 1400)
    for wrong in ("inbound", "outbound"):
        fresh = {**state, "reported": []}
        hits = events.target_hits(fresh, T0 + 1500, {**CFG, "direction": wrong}, T0, T0 + 1400)
        assert [h["vehicle"] for h in hits] == ["V1"], wrong


def test_a_bus_seen_at_the_target_before_the_boarding_stop_does_not_count() -> None:
    state = trips.new_state()
    run_bus(state, "V1", {"s4": T0 + 300, "s2": T0 + 900}, T0, T0 + 1000)  # an earlier lap passed s4, then s2
    assert events.target_hits(state, T0 + 1500, CFG, T0, T0 + 2000) == []


def test_buses_left_over_from_yesterday_are_not_logged_as_ending_early() -> None:
    """Polling stops at 09:30 and starts again next morning; the old trips must not 'end' mid-route then."""
    state = trips.new_state()
    run_bus(state, "V1", {"s2": T0 + 600, "s3": T0 + 900}, T0, T0 + 1000)  # last seen at s3, 3 stops short... of nothing
    run_bus(state, "V2", {"s2": T0 + 600, "s3": T0 + 900}, T0, T0 + 1000)
    next_morning = T0 + 24 * 3600
    trips.update_trips(state, [], POS.get, next_morning)  # the first poll the next day: both are long gone
    assert events.early_terminations(state, END, NAMES) == []
    # and a bus that comes back going the other way after the gap is not a 'turned back' either
    state = trips.new_state()
    run_bus(state, "V1", {"s2": T0 + 600, "s3": T0 + 900}, T0, T0 + 1000)
    run_bus(state, "V1", {"i0": next_morning + 300, "i1": next_morning + 500}, next_morning, next_morning + 100)
    assert events.early_terminations(state, END, NAMES) == []


TOWARDS = {"outbound": "Sutton", "inbound": "Kingston"}


def test_an_early_end_says_which_way_the_bus_was_heading() -> None:
    state = trips.new_state()
    run_bus(state, "V1", {"s2": T0 + 600, "s3": T0 + 900}, T0, T0 + 1000)
    run_bus(state, "V1", {"i0": T0 + 1300, "i1": T0 + 1500}, T0 + 1100, T0 + 1250)
    (e,) = events.early_terminations(state, END, NAMES, towards=TOWARDS)
    assert (e["stop"], e["towards"], e["kind"]) == ("Third Stop", "Sutton", "turned back")
    assert e["resumed_stop"] == "Back Start"  # and it came back the other way


def test_anomalies_say_where_and_which_way() -> None:
    state = trips.new_state()
    state["trips"]["V1"] = {"stops": [], "dir": "outbound"}
    names = NAMES | {"s1": "Second Stop", "s2": "Mid Stop", "i0": "Back Start"}
    anoms: list[dict[str, Any]] = []
    for sid in ("s1", "s2", "s1", "i0"):  # on a stop, back a stop (same direction), then the other way
        state["trips"]["V1"]["stops"].append({"sid": sid, "pos": POS[sid][1], "expected": T0})
        anoms += trips.detect_anomalies(state, lambda s: POS[s][0], names, TOWARDS)
    by = {a["type"]: a for a in anoms}
    assert by["reverse_movement"]["stop"] == "Second Stop" and by["reverse_movement"]["from_stop"] == "Mid Stop"
    assert by["reverse_movement"]["towards"] == "Sutton"  # same direction: not a return journey
    assert by["direction_change"]["towards"] == "Kingston" and by["direction_change"]["from_towards"] == "Sutton"
    assert by["direction_change"]["stop"] == "Back Start"


def test_each_direction_is_labelled_by_where_it_ends() -> None:
    from app.integrations.bus.poller import Lookup

    seq = [{"direction": "outbound", "pos": p, "sid": f"o{p}", "name": n} for p, n in enumerate(["Kingston", "Worcester Park", "Sutton"])]
    seq += [{"direction": "inbound", "pos": p, "sid": f"i{p}", "name": n} for p, n in enumerate(["Sutton", "Worcester Park", "Kingston"])]
    assert Lookup(seq).towards == {"outbound": "Sutton", "inbound": "Kingston"}


def test_a_scheduled_time_at_the_target_for_each_departure() -> None:
    from app.integrations.bus.events import journey_rows, pair_arrivals

    due = pair_arrivals(["07:02", "07:14", "07:26"], ["07:09", "07:21", "07:33", "07:45"])
    assert due == {"07:02": "07:09", "07:14": "07:21", "07:26": "07:33"}  # in order, none used twice
    hits = [{"slot": "07:02", "board_at": "L1", "arrive_at": "A1"}, {"slot": None, "board_at": "L2", "arrive_at": "A2"}]
    clock = {"L1": "07:04", "A1": "07:12", "L2": "07:40", "A2": "07:55"}
    rows = journey_rows(["07:02", "07:14", "07:26"], hits, due, 7 * 60 + 50, clock.get)
    assert [(r["slot"], r["status"]) for r in rows] == [("07:02", "arrived"), ("07:14", "missed"), ("07:26", "waiting"), (None, "arrived")]
    assert rows[0]["late_min"] == 3 and rows[0]["due_arrive"] == "07:09" and rows[1]["late_min"] is None
    over = journey_rows(["07:26"], [], due, None, clock.get)  # a finished day: no bus is still waiting
    assert over[0]["status"] == "missed"
