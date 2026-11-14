"""The school run: a bus that reaches the school, one that turns at the A3 roundabout, one that leaves the route and
misses the school, one that reaches it from the other side of the road, timetabled journeys not seen (and not known
because no positions came in), TfL's predictions read as arrivals, drops and delays, and the reliability over days."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.modules.buslog import run

LAT = 51.40
DAY = datetime(2026, 11, 16, tzinfo=UTC)
M_PER_DEG = 111_320 * 0.62479  # metres per degree of longitude at 51.4°N (cos 51.4°)


def lon(m: float) -> float:
    return -0.30 + m / M_PER_DEG


NAMES = ["Sutton Bus Garage", "Cheam", "Worcester Park", "Blakes Lane", "St James Ch /Kingston By-Pass",
         "New Malden / Fountain Roundabout", "Coombe Girls School", "Oak Road", "Fairfield Bus Station"]  # fmt: skip
ROUTE = [{"stop": f"S{i}", "name": n, "lat": LAT + 0.00005, "lon": lon(i * 700)} for i, n in enumerate(NAMES)]
OTHER = [{"stop": "S6E", "name": "Coombe Girls School", "lat": LAT - 0.00005, "lon": lon(6 * 700)}]
SHAPE = [[lon(m), LAT] for m in range(0, 8 * 700 + 1, 100)]


def at(minutes: float) -> datetime:
    return DAY.replace(hour=7) + timedelta(minutes=minutes)


def drive(start_min: float, metres: list[float], off: dict[int, float] | None = None) -> list[dict[str, object]]:
    """A fix a minute at each distance along the line (off: fix index -> metres north of it)."""
    out = []
    for i, m in enumerate(metres):
        north = (off or {}).get(i, 0) / 110_540
        out.append({"recorded_at": at(start_min + i), "lat": LAT + north, "lon": lon(m)})
    return out


def timetable() -> list[dict[str, object]]:
    rows = []
    for j, first in enumerate((10, 20, 30, 40, 50, 60), start=1):  # 07:10 ... 08:00 at Worcester Park (S2)
        for i in range(9):
            rows.append({"journey": j, "stop": f"S{i}", "scheduled_at": at(first + (i - 2) * 1.4)})
    return rows


def test_a_morning() -> None:
    fwd = [1050 + 500 * k for k in range(10)]  # from just before Worcester Park to the end (1400 m)
    pos = {
        # journey 1: all the way
        "MADE": drive(9.5, fwd),
        # journey 2: past Blakes Lane and the A3 roundabout, then back the way it came
        "TURN": drive(19.5, [1050, 1550, 2050, 2550, 2900, 2600, 2100, 1600, 1100]),
        # journey 3: leaves the route after the Fountain, back on it past the school, carrying on to Kingston
        "SKIP": drive(29.5, [1050, 1550, 2050, 2550, 3050, 3700, 4300, 5000, 5600], off={5: 600, 6: 700}),
        # journey 4: leaves after the Fountain, comes back past the school heading towards Sutton
        "BACK": drive(39.5, [1050, 1550, 2050, 2550, 3050, 3700, 5000, 4500, 4000, 3500], off={5: 600, 6: 700}),
    }
    preds = [
        # TfL at the school for MADE: first 07:16 (on time), then 07:23: 8 minutes late, dropped as it got there
        {"seen_at": at(0), "vehicle": "MADE", "stop": "S6", "change": "new", "expected_at": at(15.6)},
        {"seen_at": at(12), "vehicle": "MADE", "stop": "S6", "change": "moved", "expected_at": at(23.6)},
        {"seen_at": at(23.8), "vehicle": "MADE", "stop": "S6", "change": "gone", "expected_at": None},
        # for TURN at the school: dropped while it was heading back (not an arrival)
        {"seen_at": at(15), "vehicle": "TURN", "stop": "S6", "change": "new", "expected_at": at(26)},
        {"seen_at": at(24), "vehicle": "TURN", "stop": "S6", "change": "gone", "expected_at": None},
    ]
    gaps = [(at(58), at(63))]  # no positions answered around 08:00: journey 6 is not known
    out = run.morning(ROUTE, SHAPE, OTHER, timetable(), pos, preds, "S2", "S6", "S6E", (at(0), at(120)),
                      by=at(85), gaps=gaps)  # fmt: skip
    b = {x["vehicle"]: x for x in out["buses"] if x["vehicle"]}

    made = b["MADE"]
    assert made["journey"] == 1 and made["outcome"] == "made_it" and made["in_time"]
    assert made["arrived_school_actual"] and abs((made["arrived_school"] - at(23.6)).total_seconds()) <= 30
    assert made["delay"]["late_s"] >= 300 and made["expected_school"] == at(23.6) and made["first_expected_school"] == at(15.6)
    assert made["left_first"] is not None and not made["left_first_actual"]  # GPS only: probable

    turn = b["TURN"]
    assert turn["outcome"] == "turned" and turn["words"] == "turned at the A3 roundabout (St James Ch /Kingston By-Pass)"
    school_row = next(r for r in turn["stops"] if r["stop"] == "S6")
    assert school_row["actual"] is None and school_row["note"].startswith("not reached") and "TfL dropped it" in school_row["note"]
    assert not turn["made_it"]

    skip = b["SKIP"]
    assert skip["outcome"] == "skipped" and "missed the school" in skip["words"]
    assert "Coombe Girls School" in skip["off_route"][0]["missed"]

    back = b["BACK"]
    assert back["outcome"] == "other_side" and back["made_it"] and "heading back towards Sutton" in back["words"]

    rest = {x["journey"]: x["outcome"] for x in out["buses"] if not x["vehicle"]}
    assert rest == {5: "not_seen", 6: "no_data"}
    s = out["summary"]
    assert (s["timetabled"], s["recorded"], s["made_it"], s["other_side"], s["turned"], s["skipped"], s["not_seen"], s["no_data"]) == (6, 4, 2, 1, 1, 1, 1, 1)
    assert s["percent"] == 50
    kinds = [e["kind"] for e in out["events"] if e["vehicle"] == "TURN"]
    assert "turned" in kinds and "left" in kinds


def test_the_journey_is_the_bus_own_when_bods_says() -> None:
    pos = {"LATE": [{**f, "origin_aimed_departure": at(10 - 2.8)} for f in drive(26.5, [1050 + 500 * k for k in range(9)])]}
    out = run.morning(ROUTE, SHAPE, OTHER, timetable(), pos, [], "S2", "S6", "S6E", (at(0), at(120)))
    bus = next(x for x in out["buses"] if x["vehicle"])
    assert bus["journey"] == 1  # 17 minutes late for the 07:10, not the 07:30 it is nearest to


def test_tfl_at_a_stop() -> None:
    rows = [{"seen_at": at(0), "change": "new", "expected_at": at(10)}, {"seen_at": at(5), "change": "moved", "expected_at": at(12)},
            {"seen_at": at(12.5), "change": "gone", "expected_at": None}]  # fmt: skip
    t = run.tfl_at_stop(rows, at(6))
    assert t["first_expected"] == at(10) and t["last_expected"] == at(12) and t["arrived"] == at(12)
    assert t["late"] == {"seen_at": at(5), "late_s": 360, "expected_at": at(12)}  # 4 minutes late is not said; 6 is
    dropped = run.tfl_at_stop([rows[0], {"seen_at": at(3), "change": "gone", "expected_at": None}], None)
    assert dropped["arrived"] is None and dropped["dropped_at"] == at(3)


def test_reliability_over_days() -> None:
    days = [{"day": "2026-11-16", "summary": {"recorded": 10, "made_it": 7, "in_time": 6, "turned": 2}},
            {"day": "2026-11-17", "summary": {"recorded": 10, "made_it": 9, "in_time": 9, "skipped": 1}}]  # fmt: skip
    r = run.reliability(days)
    assert r["total"]["recorded"] == 20 and r["total"]["made_it"] == 16 and r["total"]["percent"] == 80
    assert r["total"]["in_time_percent"] == 75
