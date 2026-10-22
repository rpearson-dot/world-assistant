"""The setup checklist's rules: what counts as working, missing, failing or off."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from app.mqtt_config import MqttConf
from app.setup_checklist import SOURCES, home_assistant, judge

NOW = datetime(2026, 10, 22, 17, 0, tzinfo=UTC)
BY_ID = {s.id: s for s in SOURCES}


def settings(**kw: Any) -> Any:
    base = {"nh_poll_enabled": True, "fuel_poll_enabled": True, "energy_poll_enabled": True, "flood_poll_enabled": True,
            "tflroads_poll_enabled": True, "naptan_poll_enabled": True, "codepoint_poll_enabled": True,
            "mqtt_active": True, "ha_publish": True, "mqtt_host": "broker.lan", "mqtt_port": 1883}  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


def run(status: str = "ok", minutes: int = 5, error: str | None = None) -> dict[str, Any]:
    t = NOW - timedelta(minutes=minutes)
    return {"status": status, "started_at": t, "finished_at": t + timedelta(seconds=3), "error": error}


def test_a_source_without_its_key_is_not_set_up_and_says_where_to_get_one() -> None:
    row = judge(BY_ID["nh"], settings(), {})
    assert row["state"] == "missing" and row["missing_keys"] == ["nh_subscription_key"]
    assert row["link"].startswith("https://")


def test_set_up_and_working_failing_or_waiting() -> None:
    s = settings(nh_subscription_key="k")
    assert judge(BY_ID["nh"], s, {})["state"] == "waiting"  # key there, no run yet
    ok = judge(BY_ID["nh"], s, {"nh_poll_live": run()})
    assert ok["state"] == "ready" and ok["last_ok"].startswith("2026-10-22T16:55")
    bad = judge(BY_ID["nh"], s, {"nh_poll_live": run("error", 1, "HTTPStatusError: 401")})
    assert bad["state"] == "failing" and "401" in bad["error"]


def test_both_halves_of_a_login_are_needed_but_either_events_key_will_do() -> None:
    assert judge(BY_ID["fuel"], settings(fuel_finder_client_id="id"), {})["state"] == "missing"
    assert judge(BY_ID["events"], settings(skiddle_api_key="k"), {})["state"] == "ready"
    assert judge(BY_ID["events"], settings(), {})["state"] == "missing"


def test_tfl_works_without_a_key_but_better_with_one() -> None:
    assert judge(BY_ID["tfl"], settings(), {"tflroads": run()})["state"] == "limited"
    assert judge(BY_ID["tfl"], settings(tfl_app_key="k"), {"tflroads": run()})["state"] == "ready"


def test_octopus_rates_need_only_a_tariff_code() -> None:
    assert judge(BY_ID["octopus"], settings(octopus_tariff="E-1R-COSY-22-12-08-A"), {"energy": run()})["state"] == "ready"


def test_sources_with_no_key_and_switches() -> None:
    assert judge(BY_ID["floods"], settings(), {"flood": run()})["state"] == "ready"
    assert judge(BY_ID["floods"], settings(flood_poll_enabled=False), {"flood": run()})["state"] == "off"
    assert judge(BY_ID["naptan"], settings(), {})["state"] == "waiting"


def test_home_assistant_states() -> None:
    on, off = MqttConf("broker.lan", 8883, "wa", True), MqttConf("", 1883, "", False)
    assert home_assistant(settings(), None, off)["state"] == "off"
    waiting = home_assistant(settings(), None, on)
    assert waiting["state"] == "waiting" and waiting["broker"] == "broker.lan:8883" and waiting["tls"] is True
    ok = home_assistant(settings(), {"ok": True, "at": NOW.isoformat(), "sensors": 40}, on)
    assert ok["state"] == "ready" and ok["note"] == "40 sensors on broker.lan:8883, encrypted."
    bad = home_assistant(settings(), {"ok": False, "error": "OSError: refused"}, on)
    assert bad["state"] == "failing" and "refused" in bad["error"]


def test_every_source_says_what_it_unlocks() -> None:
    assert all(s.unlocks and s.name for s in SOURCES)
    assert all(s.link.startswith("https://") for s in SOURCES if s.keys)
