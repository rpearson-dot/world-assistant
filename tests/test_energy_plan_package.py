"""The Home Assistant energy plan package: its slot check, rendered with Jinja as Home Assistant would."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

yaml = pytest.importorskip("yaml")
jinja2 = pytest.importorskip("jinja2")

PLAN = yaml.safe_load((Path(__file__).parent.parent / "homeassistant/packages/wa_energy_plan.yaml").read_text())
GOOD = {f"text.office_inverter_time_slot_{n}_start": t
        for n, t in zip("123456", ["00:00", "04:00", "07:00", "13:00", "16:00", "22:00"], strict=True)}  # fmt: skip
GOOD |= {f"switch.office_inverter_time_slot_{n}_on": "on" for n in "246"}
GOOD |= {"input_boolean.wa_energy_plan_apply": "on", "switch.office_inverter_allow_remote_control": "on"}


def _render(states: dict[str, str]) -> tuple[str, str, bool, str]:
    env = jinja2.Environment()
    check = PLAN["template"][0]["sensor"][0]
    funcs: dict[str, Any] = {"states": lambda e: states.get(e, "unknown"), "is_state": lambda e, v: states.get(e) == v,
                             "state_attr": lambda e, a: states.get(f"{e}#{a}")}  # fmt: skip
    state = env.from_string(check["state"]).render(**funcs).strip()
    problems = env.from_string(check["attributes"]["problems"]).render(**funcs).strip()
    states |= {"sensor.wa_inverter_slot_check": state, "sensor.wa_inverter_slot_check#problems": problems}
    auto = next(a for a in PLAN["automation"] if a["id"] == "wa_energy_plan_1230")
    apply = env.from_string(auto["variables"]["apply"]).render(**funcs).strip() == "True"
    msg = env.from_string(auto["actions"][0]["data"]["message"]).render(evening=75, evening_now=70, apply=apply,
                                                                       slots=state, **funcs)  # fmt: skip
    return state, problems, apply, " ".join(msg.split())


def test_slots_as_expected_apply() -> None:
    assert _render(dict(GOOD)) == ("ok", "", True, "Slot 4 (13:00) target 75% (now 70%). Applying.")


def test_a_changed_slot_is_reported_and_nothing_applied() -> None:
    changed = GOOD | {"text.office_inverter_time_slot_6_start": "23:00:00", "switch.office_inverter_time_slot_4_on": "off"}
    state, problems, apply, msg = _render(changed)
    assert (state, apply) == ("mismatch", False)
    assert problems == "slot 6 starts 23:00, expected 22:00; slot 4 grid charge is off"
    assert "Not applied: the inverter's slots have changed (slot 6 starts 23:00" in msg


def test_an_integration_without_slot_times_is_not_blocked() -> None:
    state, _, apply, msg = _render({k: v for k, v in GOOD.items() if not k.startswith("text.")})
    assert (state, apply) == ("unchecked", True) and "could not be read" in msg


def test_both_planning_automations_respect_the_check() -> None:
    for aid in ("wa_energy_plan_2100", "wa_energy_plan_1230"):
        auto = next(a for a in PLAN["automation"] if a["id"] == aid)
        assert "wa_inverter_slot_check" in auto["variables"]["apply"]


def test_switched_on_but_remote_control_off_or_missing_says_why() -> None:
    _, _, apply, msg = _render(GOOD | {"switch.office_inverter_allow_remote_control": "off"})
    assert not apply and "Not applied: the inverter's remote control is off" in msg
    missing = {k: v for k, v in GOOD.items() if k != "switch.office_inverter_allow_remote_control"}
    _, _, apply, msg = _render(missing)
    assert not apply and "remote control is missing" in msg
    _, _, apply, msg = _render(GOOD | {"input_boolean.wa_energy_plan_apply": "off"})
    assert not apply and msg.endswith("Dry run: nothing changed.")


# --- Free Electricity and the morning target ---------------------------------------------------------------------

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def _ha_env(states: dict[str, Any], now: datetime) -> tuple[Any, dict[str, Any]]:
    import json

    env = jinja2.Environment(extensions=["jinja2.ext.do"])
    as_dt = lambda v, default=None: datetime.fromisoformat(v) if isinstance(v, str) and v[:2] == "20" else default
    env.filters |= {"as_local": lambda d: d.astimezone(LONDON), "from_json": json.loads, "to_json": json.dumps,
                    "int": lambda v, d=0: int(float(v)) if str(v).replace(".", "", 1).isdigit() else d,
                    "float": lambda v, d=0.0: float(v) if str(v).replace(".", "", 1).isdigit() else d,
                    "string": str, "as_datetime": as_dt}  # fmt: skip
    funcs = {"states": lambda e: str(states.get(e, "unknown")), "is_state": lambda e, v: states.get(e) == v,
             "state_attr": lambda e, a: states.get(f"{e}#{a}"), "as_datetime": as_dt, "now": lambda: now,
             "timedelta": timedelta, "none": None}  # fmt: skip
    return env, funcs


def _free(start: str, joined: bool = True) -> dict[str, Any]:
    end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
    return {"sensor.wa_energy_free_electricity_next_start": start,
            "sensor.wa_energy_free_electricity_next_start#end": end,
            "sensor.wa_energy_free_electricity_next_start#joined": joined,
            "sensor.wa_energy_free_electricity_next_start#type": "Weekend Happy Hour"}  # fmt: skip


def _vars(auto_id: str, states: dict[str, Any], now: datetime) -> dict[str, Any]:
    """The automation's variables, rendered in order as Home Assistant does (each sees the ones before)."""
    env, funcs = _ha_env(states, now)
    auto = next(a for a in PLAN["automation"] if a["id"] == auto_id)
    out: dict[str, Any] = {}
    for k, tpl in auto["variables"].items():
        text_ = env.from_string(tpl).render(**funcs, **out).strip()
        try:
            import ast

            out[k] = ast.literal_eval(text_)
        except (ValueError, SyntaxError):
            out[k] = text_
    return out


def test_a_joined_free_hour_picks_its_slot_and_keeps_what_to_put_back() -> None:
    sunday = datetime(2026, 10, 25, 12, 59, tzinfo=UTC)  # GMT from 02:00 on 25 October
    states = dict(GOOD) | _free("2026-10-25T13:00:00+00:00") | {
        "number.office_inverter_time_slot_4_capacity": "65", "switch.office_inverter_time_slot_4_on": "on",
        "input_number.wa_free_target_pct": "100", "sensor.wa_inverter_slot_check": "ok", "input_text.wa_free_restore": ""}  # fmt: skip
    v = _vars("wa_energy_free_start", states, sunday)
    assert (v["slot"], v["target"], v["cap_now"], v["on_now"], v["joined"], v["apply"]) == (4, 80, 65, "on", True, True)
    assert v["saved"] == {}
    # an hour in the 07:00-13:00 window, whose grid charge is normally off
    states |= _free("2026-10-25T10:00:00+00:00") | {"number.office_inverter_time_slot_3_capacity": "10",
                                                    "switch.office_inverter_time_slot_3_on": "off"}  # fmt: skip
    v = _vars("wa_energy_free_start", states, sunday)
    assert (v["slot"], v["cap_now"], v["on_now"]) == (3, 10, "off")
    # summer time: 13:00 BST is 12:00 UTC, still slot 4
    assert _vars("wa_energy_free_start", states | _free("2026-06-07T12:00:00+00:00"), sunday)["slot"] == 4
    # the value saved to put back: back-to-back hours in the same slot keep the first one's settings
    auto = next(a for a in PLAN["automation"] if a["id"] == "wa_energy_free_start")
    save = auto["actions"][1]["then"][1]["data"]["value"]
    env, funcs = _ha_env(states, sunday)
    first = env.from_string(save).render(**funcs, saved={}, slot=4, cap_now=65, on_now="on", end="E1").strip()
    assert json_loads(first) == {"slot": 4, "cap": 65, "on": "on", "end": "E1"}
    again = env.from_string(save).render(**funcs, saved=json_loads(first), slot=4, cap_now=100, on_now="on", end="E2")
    assert json_loads(again) == {"slot": 4, "cap": 65, "on": "on", "end": "E2"}


def test_a_free_hour_you_did_not_join_does_nothing() -> None:
    env, _ = _ha_env({}, datetime(2026, 10, 25, 6, 59, tzinfo=UTC))
    auto = next(a for a in PLAN["automation"] if a["id"] == "wa_energy_free_start")
    cond = auto["conditions"][0]["value_template"]
    assert env.from_string(cond).render(joined=False, end="x", on_now="on").strip() == "False"
    assert env.from_string(cond).render(joined=True, end="x", on_now="unavailable").strip() == "False"
    assert env.from_string(cond).render(joined=True, end="x", on_now="off").strip() == "True"


def test_the_morning_target_only_covers_the_hours_before_a_joined_free_hour() -> None:
    sensor = next(x for x in PLAN["template"][0]["sensor"] if x["unique_id"] == "wa_wanted_charge_morning")
    base = {"input_number.wa_battery_kwh": "30", "input_number.wa_floor_pct": "15", "input_number.wa_morning_load_kw": "1.2",
            "input_number.wa_margin": "1.25", "input_number.wa_min_target_pct": "25", "input_number.wa_max_target_pct": "95",
            "input_number.wa_solar_morning_share": "0.5", "sensor.energy_production_tomorrow": "0"}  # fmt: skip
    at_nine = datetime(2026, 10, 24, 20, 0, tzinfo=UTC)  # 21:00 BST, the evening before

    def target(extra: dict[str, Any]) -> int:
        env, funcs = _ha_env(base | extra, at_nine)
        return int(env.from_string(sensor["state"]).render(**funcs).strip())

    assert target({}) == 45  # 15% + 1.2 kW x 6 h x 1.25 = 9 kWh = 30%
    assert target(_free("2026-10-25T09:00:00+00:00")) == 25  # free from 09:00: 2 h = 3 kWh = 10%, floored at 25%
    assert target(_free("2026-10-25T09:00:00+00:00", joined=False)) == 45
    assert target(_free("2026-10-25T13:00:00+00:00")) == 45  # the afternoon hour changes nothing here
    assert target({"sensor.energy_production_tomorrow": "7.8"}) == 30  # less half of tomorrow's solar


def json_loads(s: str) -> Any:
    import json

    return json.loads(s.strip())


def test_targets_stop_at_the_battery_health_max_and_say_what_was_wanted() -> None:
    sensors = {x["unique_id"]: x for x in PLAN["template"][0]["sensor"]}
    evening = sensors["wa_target_charge_evening"]
    for wanted, cap, applied, over in (("95", "80", 80, "True"), ("65", "80", 65, "False"), ("95", "100", 95, "False")):
        env, funcs = _ha_env({"sensor.wa_wanted_charge_evening": wanted, "input_number.wa_max_target_pct": cap},
                             datetime(2026, 10, 24, 11, 30, tzinfo=UTC))  # fmt: skip
        assert int(env.from_string(evening["state"]).render(**funcs).strip()) == applied
        assert env.from_string(evening["attributes"]["over_health_max"]).render(**funcs).strip() == over
    # the wanted level is no longer capped by the health max (only at 100%)
    wanted = sensors["wa_wanted_charge_evening"]
    env, funcs = _ha_env({"input_number.wa_battery_kwh": "30", "input_number.wa_floor_pct": "15",
                          "input_number.wa_evening_load_kw": "3.5", "input_number.wa_margin": "1.25",
                          "input_number.wa_min_target_pct": "25"}, datetime(2026, 10, 24, 11, 30, tzinfo=UTC))  # fmt: skip
    assert int(env.from_string(wanted["state"]).render(**funcs).strip()) == 100  # 15% + 26 kWh: more than the battery
    # the suggestion names the slot, the window and the level wanted
    auto = next(a for a in PLAN["automation"] if a["id"] == "wa_energy_health_max_suggestion")
    env, funcs = _ha_env({"sensor.wa_target_charge_evening#over_health_max": True, "sensor.wa_target_charge_evening#slot": 4,
                          "sensor.wa_target_charge_evening#covers": "16:00-22:00",
                          "sensor.wa_target_charge_evening#wanted": "95"}, datetime(2026, 10, 24, 11, 31, tzinfo=UTC))  # fmt: skip
    said = " ".join(env.from_string(auto["variables"]["capped"]).render(**funcs).split())
    assert said == "slot 4 (for 16:00-22:00) wants 95%"


def test_free_hours_never_charge_above_the_health_max() -> None:
    states = dict(GOOD) | _free("2026-10-25T13:00:00+00:00") | {
        "number.office_inverter_time_slot_4_capacity": "65", "switch.office_inverter_time_slot_4_on": "on",
        "input_number.wa_free_target_pct": "100", "input_number.wa_max_target_pct": "80", "input_text.wa_free_restore": ""}  # fmt: skip
    assert _vars("wa_energy_free_start", states, datetime(2026, 10, 25, 12, 59, tzinfo=UTC))["target"] == 80
