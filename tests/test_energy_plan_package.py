"""The Home Assistant energy plan package, rendered with Jinja as Home Assistant would, against a schedule that World
Assistant really builds (app.energy.schedule) from Cosy's rates."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.energy import rates as R
from app.energy.schedule import build

yaml = pytest.importorskip("yaml")
jinja2 = pytest.importorskip("jinja2")

PLAN = yaml.safe_load((Path(__file__).parent.parent / "homeassistant/packages/wa_energy_plan.yaml").read_text())
LONDON = ZoneInfo("Europe/London")
NOW = datetime(2026, 10, 24, 10, 0, tzinfo=LONDON)
COSY = [(0, 4, 29.1), (4, 7, 14.3), (7, 13, 29.1), (13, 16, 14.3), (16, 19, 40.0), (19, 22, 29.1), (22, 24, 14.3)]
GO = [(0, 0.5, 25.0), (0.5, 5.5, 8.5), (5.5, 24, 25.0)]


def rates(spec: list[tuple[float, float, float]]) -> list[R.Period]:
    out = []
    for d in range(-1, 3):
        base = (NOW + timedelta(days=d)).replace(hour=0, minute=0)
        out += [R.Period((base + timedelta(hours=a)).astimezone(UTC), (base + timedelta(hours=b)).astimezone(UTC), p)
                for a, b, p in spec]  # fmt: skip
    return R.normalise(out)


SCHED = build(rates(COSY), NOW, LONDON)["slots"]
SETTINGS = {"input_number.wa_battery_kwh": "40.0", "input_number.wa_floor_pct": "20.0", "input_number.wa_min_target_pct": "25.0",
            "input_number.wa_max_target_pct": "80.0", "input_number.wa_margin": "1.25", "input_number.wa_night_load_kw": "0.6",
            "input_number.wa_morning_load_kw": "1.2", "input_number.wa_evening_load_kw": "2.0", "input_number.wa_solar_trust": "0.8",
            "input_number.wa_session_margin": "1.5", "input_number.wa_session_extra_floor_pct": "10",
            "sensor.energy_production_today": "3.3", "sensor.energy_production_tomorrow": "7.7"}  # fmt: skip
CAPS = ["10.0", "80.0", "10.0", "70.0", "10.0", "80.0"]
INVERTER = ({f"text.office_inverter_time_slot_{s['n']}_start": s["start"] for s in SCHED}
            | {f"switch.office_inverter_time_slot_{s['n']}_on": "on" if s["charge"] else "off" for s in SCHED}
            | {f"number.office_inverter_time_slot_{n}_capacity": v for n, v in zip(range(1, 7), CAPS, strict=True)}
            | {"switch.office_inverter_allow_remote_control": "on", "input_boolean.wa_energy_plan_apply": "on",
               "input_text.wa_free_restore": "", "sensor.wa_energy_charge_schedule#slots": SCHED})  # fmt: skip


def _env(states: dict[str, Any], now: datetime = NOW) -> tuple[Any, dict[str, Any]]:
    from jinja2.sandbox import ImmutableSandboxedEnvironment  # as Home Assistant renders templates

    env = ImmutableSandboxedEnvironment()
    as_dt = lambda v, default=None: datetime.fromisoformat(v) if isinstance(v, str) and v[:2] == "20" else default
    num = lambda v, d=0: float(v) if str(v).lstrip("-").replace(".", "", 1).isdigit() else d
    env.filters |= {"as_local": lambda d: d.astimezone(LONDON), "from_json": json.loads, "to_json": json.dumps,
                    "int": lambda v, d=0: int(num(v, d)), "float": num, "as_datetime": as_dt}  # fmt: skip
    funcs = {"states": lambda e: str(states.get(e, "unknown")), "is_state": lambda e, v: states.get(e) == v,
             "state_attr": lambda e, a: states.get(f"{e}#{a}"), "as_datetime": as_dt, "now": lambda: now,
             "timedelta": timedelta, "none": None, "range": range}  # fmt: skip
    return env, funcs


def _value(text: str) -> Any:
    text = text.strip()
    try:
        return ast.literal_eval(text.replace("true", "True").replace("false", "False"))
    except (ValueError, SyntaxError):
        return text


def render(tpl: str, states: dict[str, Any], now: datetime = NOW, **ctx: Any) -> Any:
    env, funcs = _env(states, now)
    return _value(env.from_string(tpl).render(**funcs, **ctx))


def words(tpl: str, states: dict[str, Any], **ctx: Any) -> str:
    env, funcs = _env(states)
    return " ".join(env.from_string(tpl).render(**funcs, **ctx).split())


def sensor(uid: str) -> dict[str, Any]:
    return next(x for x in PLAN["template"][0]["sensor"] if x["unique_id"] == uid)


def auto(aid: str) -> dict[str, Any]:
    return next(a for a in PLAN["automation"] if a["id"] == aid)


def targets(states: dict[str, Any]) -> list[dict[str, Any]]:
    return render(sensor("wa_charge_targets")["attributes"]["slots"], states)


# --- World Assistant's schedule ----------------------------------------------------------------------------------


def test_cosy_comes_out_as_the_slots_the_inverter_already_has() -> None:
    assert [(s["start"], s["charge"]) for s in SCHED] == [("00:00", False), ("04:00", True), ("07:00", False),
                                                         ("13:00", True), ("16:00", False), ("22:00", True)]  # fmt: skip
    by = {s["n"]: s for s in SCHED}
    assert (by[2]["cover_from"], by[2]["cover_to"], by[2]["load_hours"]) == ("07:00", "13:00", {"night": 0, "day": 6, "evening": 0})
    assert by[4]["load_hours"]["evening"] == 6 and by[4]["solar_share"]["today"] > 0  # the afternoon sun after 16:00
    assert by[6]["load_hours"] == {"night": 4, "day": 0, "evening": 0} and by[6]["solar_share"] == {"today": 0, "tomorrow": 0}
    assert by[2]["solar_share"]["tomorrow"] == pytest.approx(0.5)


def test_go_flux_agile_and_a_flat_tariff() -> None:
    go = build(rates(GO), NOW, LONDON)
    assert go["summary"] == "00:30-05:30" and len(go["slots"]) == 6
    assert [s["start"] for s in go["slots"] if s["charge"]] == ["00:30"]
    assert next(s for s in go["slots"] if s["charge"])["cover_hours"] == 19.0
    starts = [s["start"] for s in go["slots"]]
    assert starts == sorted(starts) and starts[0] == "00:00"
    flux = build(rates([(0, 2, 25.0), (2, 5, 15.0), (5, 16, 25.0), (16, 19, 35.0), (19, 24, 25.0)]), NOW, LONDON)
    assert flux["summary"] == "02:00-05:00"
    agile = [(h / 2, h / 2 + 0.5, p) for h, p in enumerate([20.0] * 8 + [9.0] * 4 + [20.0] * 14 + [5.0] * 4 + [30.0] * 6
                                                            + [20.0] * 4 + [8.0] * 2 + [21.0] * 2 + [7.0] * 4)]  # fmt: skip
    a = build(rates(agile), NOW, LONDON)
    assert len(a["slots"]) == 6 and len(a["windows"]) == 3  # four cheap runs: the three cheapest kept
    assert {w["start"] for w in a["windows"]} == {"13:00", "20:00", "22:00"}  # by price: 5p, 7p, 8p (not 04:00 at 9p)
    flat = build(rates([(0, 24, 24.5)]), NOW, LONDON)
    assert flat["summary"] == "no cheap window" and not any(s["charge"] for s in flat["slots"])
    assert build([], NOW, LONDON)["available"] is False


def test_a_window_under_way_is_planned_for_today_and_free_hours_and_sessions_count() -> None:
    at_two = datetime(2026, 10, 24, 14, 0, tzinfo=LONDON)
    four = next(s for s in build(rates(COSY), at_two, LONDON)["slots"] if s["start"] == "13:00")
    assert four["solar_share"]["today"] > 0  # covers today's 16:00-22:00, while today's window is still on
    free = [{"start": "2026-10-25T09:00:00+00:00", "end": "2026-10-25T10:00:00+00:00", "joined": True},
            {"start": "2026-10-25T08:00:00+00:00", "end": "2026-10-25T09:00:00+00:00", "joined": False}]  # fmt: skip
    two = next(s for s in build(rates(COSY), NOW, LONDON, free=free)["slots"] if s["start"] == "04:00")
    assert (two["cover_to"], two["cover_hours"], two["load_hours"]["day"]) == ("09:00", 2.0, 2.0)  # GMT from 25 October
    session = [{"start": "2026-10-24T17:00:00+01:00", "end": "2026-10-24T18:00:00+01:00", "joined": True},
               {"start": "2026-10-25T05:00:00+00:00", "end": "2026-10-25T06:00:00+00:00", "joined": True}]  # fmt: skip
    plan = build(rates(COSY), NOW, LONDON, sessions=session)
    assert next(s for s in plan["slots"] if s["start"] == "13:00")["session"] is True
    assert len(plan["warnings"]) == 1 and "Sun 05:00 falls in charging slot 2" in plan["warnings"][0]


def test_the_schedule_reaches_home_assistant() -> None:
    from app.energy.ha import schedule_sensor

    (s,) = schedule_sensor({"available": True, "summary": "04:00-07:00, 13:00-16:00, 22:00-00:00", "slots": SCHED,
                            "windows": [], "warnings": [], "tariff": "E-1R-COSY"})  # fmt: skip
    assert (s.key, s.state, len(s.attributes["slots"])) == ("charge_schedule", "04:00-07:00, 13:00-16:00, 22:00-00:00", 6)
    assert schedule_sensor({"available": False, "reason": "not enough rates"})[0].state == "unavailable"
    assert schedule_sensor(None) == []


# --- the package -------------------------------------------------------------------------------------------------


def test_targets_from_the_schedule_and_the_house() -> None:
    t = {x["n"]: x for x in targets(SETTINGS | INVERTER)}
    assert (t[6]["target"], t[2]["target"], t[4]["target"]) == (30, 35, 55)  # night 3 kWh; day 9 less solar; evening 15
    assert all(t[n]["target"] == 20 and not t[n]["charge"] for n in (1, 3, 5))  # the floor
    big = {x["n"]: x for x in targets(SETTINGS | INVERTER | {"input_number.wa_evening_load_kw": "4.5"})}
    assert (big[4]["wanted"], big[4]["target"], big[4]["over"]) == (100, 80, True)
    # 13:00-16:00 can add only 37.5% (5 kW x 3 h of 40 kWh): the 04:00 window charges higher to start it at 62.5%
    assert (big[2]["wanted"], big[2]["carried"], big[2]["adds_up_to"]) == (75, True, 38)
    assert not {x["n"]: x for x in targets(SETTINGS | INVERTER)}[2]["carried"]
    joined = [dict(s, session=True) if s["n"] == 4 else s for s in SCHED]
    four = {x["n"]: x for x in targets(SETTINGS | INVERTER | {"sensor.wa_energy_charge_schedule#slots": joined})}[4]
    assert (four["wanted"], four["target"], four["session"]) == (75, 75, True)  # 20 + 10 extra + 17.7 kWh of 40 = 74
    assert targets(SETTINGS) == []  # no schedule yet


def test_slot_check_against_the_schedule() -> None:
    check = sensor("wa_inverter_slot_check")
    assert render(check["state"], INVERTER) == "ok"
    moved = INVERTER | {"text.office_inverter_time_slot_6_start": "23:00:00", "switch.office_inverter_time_slot_4_on": "off"}
    assert render(check["state"], moved) == "mismatch"
    assert words(check["attributes"]["problems"], moved) == ("slot 4 grid charge is off, the tariff wants on; "
                                                             "slot 6 starts 23:00, the tariff wants 22:00")  # fmt: skip
    borrowed = INVERTER | {"switch.office_inverter_time_slot_3_on": "on",
                           "input_text.wa_free_restore": json.dumps({"slot": 3, "cap": 10, "on": "off", "end": "x"})}  # fmt: skip
    assert render(check["state"], borrowed) == "ok"  # a free hour has borrowed slot 3
    assert render(check["state"], {k: v for k, v in INVERTER.items() if not k.startswith("text.")}) == "unchecked"
    assert render(check["state"], {}) == "no schedule"


def _plan_vars(states: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, tpl in auto("wa_energy_plan")["variables"].items():
        out[k] = render(tpl, states, **out)
    return out


def _changes(states: dict[str, Any]) -> dict[str, Any]:
    out = _plan_vars(states)
    for k, tpl in next(x for x in auto("wa_energy_plan")["actions"] if "variables" in x)["variables"].items():
        out[k] = render(tpl, states, **out)
    return out


def _ready(states: dict[str, Any], check: str = "ok") -> dict[str, Any]:
    states = states | {"sensor.wa_inverter_slot_check": check}
    return states | {"sensor.wa_charge_targets#slots": targets(states)}


def test_the_plan_sets_targets_and_the_floor() -> None:
    states = _ready(SETTINGS | INVERTER)
    v = _plan_vars(states)
    assert (v["apply"], v["earlier"], v["later"], v["switches"]) == (True, [], [], [])
    assert [(x["n"], x["from"], x["to"]) for x in _changes(states)["changes"]] == [
        (1, 10, 20), (2, 80, 35), (3, 10, 20), (4, 70, 55), (5, 10, 20), (6, 80, 30)]  # fmt: skip
    states |= {"input_text.wa_free_restore": json.dumps({"slot": 4, "cap": 70, "on": "on", "end": "x"})}
    assert 4 not in [x["n"] for x in _changes(states)["changes"]]  # a free hour has slot 4: left alone


def test_slot_times_are_written_in_an_order_that_keeps_them_sorted() -> None:
    go = build(rates(GO), NOW, LONDON)["slots"]
    v = _plan_vars(SETTINGS | INVERTER | {"sensor.wa_energy_charge_schedule#slots": go})
    times = {n: INVERTER[f"text.office_inverter_time_slot_{n}_start"] for n in range(1, 7)}
    for item in v["earlier"] + v["later"]:  # written one by one, the times stay in order at every step
        times[item["n"]] = item["start"]
        assert [times[n] for n in range(1, 7)] == sorted(times.values())
    assert [times[n] for n in range(1, 7)] == [s["start"] for s in go]
    assert {(x["n"], x["on"]) for x in v["switches"]} == {(4, False), (6, False)}  # slot 2 charges on both


def _log(states: dict[str, Any]) -> str:
    step = next(x for x in auto("wa_energy_plan")["actions"] if x.get("then") and x["then"][-1].get("action") == "logbook.log")
    return words(step["then"][-1]["data"]["message"], states, **_changes(states))


def test_the_log_says_what_would_change() -> None:
    dry = _log(_ready(SETTINGS | INVERTER | {"input_boolean.wa_energy_plan_apply": "off"}))
    assert dry.startswith("Slot 1 (00:00) 10% to 20%; Slot 2 (04:00, charging) 80% to 35%;")
    assert dry.endswith("Dry run: nothing changed.")
    moved = _ready(SETTINGS | INVERTER, "mismatch") | {
        "sensor.wa_inverter_slot_check#problems": "slot 6 starts 23:00, the tariff wants 22:00"}  # fmt: skip
    msg = _log(moved)
    assert "Not applied: the inverter's slots differ from the tariff (slot 6 starts 23:00" in msg
    assert 'Switch on "set slot times from the tariff"' in msg
    assert _log(_ready(SETTINGS | INVERTER)).endswith("Applied.")


def test_a_joined_free_hour_finds_its_slot_from_the_inverter() -> None:
    sunday = datetime(2026, 10, 25, 12, 59, tzinfo=UTC)
    free = {"sensor.wa_energy_free_electricity_next_start": "2026-10-25T13:00:00+00:00",
            "sensor.wa_energy_free_electricity_next_start#end": "2026-10-25T14:00:00+00:00",
            "sensor.wa_energy_free_electricity_next_start#joined": True, "input_number.wa_free_target_pct": "100"}  # fmt: skip
    a = auto("wa_energy_free_start")
    v: dict[str, Any] = {}
    for k, tpl in a["variables"].items():
        v[k] = render(tpl, SETTINGS | INVERTER | free, sunday, **v)
    assert (v["slot"], v["target"], v["cap_now"], v["on_now"], v["joined"]) == (4, 80, 70, "on", True)
    assert render(a["variables"]["slot"], INVERTER, sunday, start="2026-10-25T23:00:00+00:00") == 6
    assert render(a["variables"]["slot"], INVERTER, sunday, start="2026-10-25T10:00:00+00:00") == 3  # mid-slot
    cond = a["conditions"][0]["value_template"]
    assert render(cond, {}, joined=False, end="x", on_now="on") is False
    assert render(cond, {}, joined=True, end="x", on_now="off") is True


def test_the_health_max_suggestion_names_the_slot() -> None:
    a = auto("wa_energy_health_max_suggestion")
    st = {"sensor.wa_charge_targets#slots": [{"n": 4, "start": "13:00", "covers": "16:00-22:00", "wanted": 95, "over": True},
                                             {"n": 2, "start": "04:00", "covers": "07:00-13:00", "wanted": 40, "over": False}]}  # fmt: skip
    assert render(a["triggers"][0]["value_template"], st) is True
    assert words(a["variables"]["capped"], st) == "slot 4 (13:00, for 16:00-22:00) wants 95%"


def test_the_charge_rate_comes_from_the_inverter() -> None:
    rate = sensor("wa_battery_charge_rate")["state"]
    deye = {"number.office_inverter_grid_charge_current": "120.0", "number.office_inverter_battery_max_charge_current": "115.0",
            "sensor.office_inverter_battery_voltage": "52.7", "sensor.office_inverter_rated_power": "5000.0"}  # fmt: skip
    assert render(rate, deye) == 4.8  # 115 A x 52.7 V = 6.1 kW, but the inverter is rated 5 kW; less 5%
    assert render(rate, deye | {"sensor.office_inverter_rated_power": "8000"}) == 5.8  # 115 A (the battery's limit)
    assert render(rate, {"input_number.wa_charge_kw": "3.6"}) == 3.6  # nothing readable: the setting
    slow = SETTINGS | INVERTER | {"sensor.wa_battery_charge_rate": "2.0"}  # 2 kW: 22:00-00:00 adds only 10%
    t = {x["n"]: x for x in targets(slow)}
    assert t[6]["adds_up_to"] == 10 and t[4]["carried"] and t[4]["target"] > 55  # higher than at 5 kW


def test_the_schedule_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.energy import store
    from app.main import app

    async def load(engine: object) -> tuple[str, list[R.Period]]:  # Cosy around the real clock
        today = datetime.now(LONDON).replace(hour=0, minute=0, second=0, microsecond=0)
        return "E-1R-COSY", R.normalise([
            R.Period((today + timedelta(days=d, hours=a)).astimezone(UTC), (today + timedelta(days=d, hours=b)).astimezone(UTC), p)
            for d in range(-1, 3) for a, b, p in COSY])  # fmt: skip

    async def sessions(engine: object) -> list[dict[str, Any]]:
        return []

    monkeypatch.setenv("API_TOKEN", "tok")
    get_settings.cache_clear()
    monkeypatch.setattr(store, "load", load)
    monkeypatch.setattr(store, "load_sessions", sessions)
    app.state.engine = object()
    d = TestClient(app).get("/v1/energy/schedule", headers={"Authorization": "Bearer tok"}).json()
    assert d["available"] and d["tariff"] == "E-1R-COSY" and len(d["slots"]) == 6
    assert [s["start"] for s in d["slots"] if s["charge"]] == ["04:00", "13:00", "22:00"]
    get_settings.cache_clear()
