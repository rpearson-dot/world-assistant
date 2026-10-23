"""The Home Assistant energy plan package: its slot check, rendered with Jinja as Home Assistant would."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

yaml = pytest.importorskip("yaml")
jinja2 = pytest.importorskip("jinja2")

PLAN = yaml.safe_load((Path(__file__).parent.parent / "docs/ha/wa_energy_plan.yaml").read_text())
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
