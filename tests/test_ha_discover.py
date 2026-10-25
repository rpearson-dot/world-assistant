"""Find my setup: names from Home Assistant's registry, and the checks, against a registry shaped like a real one."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app import ha_names as N
from app.ha_discover import inventory, names_from


def _store(key: str, data: dict[str, Any]) -> str:
    return json.dumps({"version": 1, "minor_version": 1, "key": key, "data": data})


def _e(entity_id: str, platform: str, unique_id: str = "", **kw: Any) -> dict[str, Any]:
    return {"entity_id": entity_id, "platform": platform, "unique_id": unique_id or entity_id, "disabled_by": None, **kw}


ENTITIES = [
    _e("sensor.garage_deye_battery_soc", "sunsynk"), _e("number.garage_deye_time_slot_1_capacity", "sunsynk"),
    _e("switch.wallbox_charge_control", "ocpp"), _e("sensor.wallbox_status_connector", "ocpp"),
    _e("device_tracker.ab12cde_location", "ovms"), _e("sensor.ab12cde_12v_battery_voltage", "ovms"),
    _e("sensor.energy_production_today_2", "forecast_solar", "01J_energy_production_today"),
    _e("sensor.energy_production_tomorrow_2", "forecast_solar", "01J_energy_production_tomorrow"),
    _e("sensor.energy_production_today_remaining_2", "forecast_solar", "01J_energy_production_today_remaining"),
    _e("sensor.power_production_now_2", "forecast_solar", "01J_power_production_now"),
    _e("sensor.wa_energy_rate", "mqtt", "wa_energy_rate"),
    _e("sensor.wa_energy_charge_schedule_2", "mqtt", "wa_energy_charge_schedule"),  # renamed after a clash
    _e("sensor.old_thing", "mqtt", "x", disabled_by="user"),
]  # fmt: skip
ENTRIES = [
    {"domain": "mqtt", "title": "Mosquitto", "data": {"broker": "10.0.2.49", "port": 1883, "password": "SECRET"}},
    {"domain": "ovms", "title": "OVMS", "data": {"vehicle_id": "AB12CDE", "password": "SECRET"}},
    {"domain": "ocpp", "title": "OCPP", "data": {}},
    {"domain": "forecast_solar", "title": "Roof", "data": {"api_key": "SECRET"}},
]
RAW = {
    ".HA_VERSION": "2026.10.2\n",
    "core.config": _store("core.config", {"latitude": 51.38, "longitude": -0.24, "location_name": "Home",
                                          "time_zone": "Europe/London", "country": "GB", "currency": "GBP"}),
    "core.config_entries": _store("core.config_entries", {"entries": ENTRIES}),
    "core.entity_registry": _store("core.entity_registry", {"entities": ENTITIES}),
    "core.device_registry": _store("core.device_registry", {"devices": [{"id": "d1", "manufacturer": "Sync EV"}]}),
    "zone": _store("zone", {"items": [{"name": "Work", "latitude": 51.5, "longitude": -0.1, "radius": 100}]}),
    "person": _store("person", {"items": [{"name": "Rob"}, {"name": "Suzie"}]}),
    "lovelace_dashboards": _store("lovelace_dashboards", {"items": [{"title": "Home charging", "mode": "storage"}]}),
}  # fmt: skip


def test_names_render_into_the_files() -> None:
    body = ("sensor.office_inverter_battery_soc switch.charger_charge_control sensor.charger_status_connector "
            "sensor.nissan_leaf_hv66bzd_battery_level device_tracker.hv66bzd_location sensor.energy_production_today "
            "sensor.energy_production_today_remaining # a charger named \"charger\"")  # fmt: skip
    assert set(N.uses(body)) == {"inverter", "ocpp_charger", "ovms_vehicle", "solar_today", "solar_remaining"}
    out = N.render(body, {"inverter": "garage_deye", "ocpp_charger": "wallbox", "ovms_vehicle": "ab12cde",
                          "solar_today": "sensor.energy_production_today_2",
                          "solar_remaining": "sensor.energy_production_today_remaining_2"})  # fmt: skip
    assert out == ("sensor.garage_deye_battery_soc switch.wallbox_charge_control sensor.wallbox_status_connector "
                   "sensor.nissan_leaf_ab12cde_battery_level device_tracker.ab12cde_location sensor.energy_production_today_2 "
                   "sensor.energy_production_today_remaining_2 # a charger named \"charger\"")  # fmt: skip
    assert N.render(body, {}) == body


def test_names_are_checked() -> None:
    assert N.clean("inverter", "sensor.garage_deye_battery_soc") == "garage_deye"
    assert N.clean("ocpp_charger", "switch.wallbox_charge_control") == "wallbox"
    assert N.clean("solar_today", "Sensor.Energy_Production_Today_2") == "sensor.energy_production_today_2"
    for key, bad in (("inverter", "Garage Inverter"), ("solar_today", "energy_production_today"), ("nope", "x")):
        with pytest.raises(ValueError):
            N.clean(key, bad)


def test_names_are_found_in_the_registry() -> None:
    found = names_from(ENTITIES, ENTRIES, dict(N.DEFAULTS))
    assert found["inverter"]["found"] == "garage_deye"
    assert found["ocpp_charger"]["found"] == "wallbox"
    assert found["ovms_vehicle"]["found"] == "ab12cde"
    assert found["solar_today"]["found"] == "sensor.energy_production_today_2"
    assert found["solar_now"]["found"] == "sensor.power_production_now_2"
    # the one in use wins when there are several
    two = [*ENTITIES, _e("sensor.office_inverter_battery_soc", "x"), _e("number.office_inverter_time_slot_1_capacity", "x")]
    assert names_from(two, ENTRIES, dict(N.DEFAULTS))["inverter"] == {
        "found": "office_inverter", "candidates": ["garage_deye", "office_inverter"], "current": "office_inverter"}


def test_inventory_checks_everything_shared() -> None:
    wanted = {"packages/wa_energy_plan.yaml": "sensor.wa_energy_charge_schedule sensor.wa_energy_rate",
              "dashboards/fuel.yaml": "views: []", "dashboards/home-charging.yaml": "new"}  # fmt: skip
    installed = {"packages/wa_energy_plan.yaml": "sensor.wa_energy_charge_schedule sensor.wa_energy_rate",
                 "dashboards/fuel.yaml": None, "dashboards/home-charging.yaml": "old"}  # fmt: skip
    inv = inventory(RAW, dict(N.DEFAULTS), installed, wanted, {"host": "10.0.2.49", "port": 1883}, (51.38, -0.24))
    by = {c["what"]: c for c in inv["checks"]}
    assert by["Home Assistant version"]["level"] == "ok" and inv["version"] == "2026.10.2"
    assert by["MQTT broker"]["level"] == "ok" and inv["mqtt"] == {"host": "10.0.2.49", "port": 1883}
    assert "sensor.wa_energy_charge_schedule_2 (expected sensor.wa_energy_charge_schedule)" in by["Renamed sensors"]["detail"]
    assert by["Sensors the packages need"]["detail"].endswith("sensor.wa_energy_charge_schedule")
    assert by["Home"]["level"] == "ok" and inv["home"]["time_zone"] == "Europe/London"
    assert by["Inverter"]["level"] == "change" and "found garage_deye" in by["Inverter"]["detail"]
    assert {f["path"]: f["state"] for f in inv["files"]} == {
        "dashboards/fuel.yaml": "not installed", "dashboards/home-charging.yaml": "different",
        "packages/wa_energy_plan.yaml": "current"}  # fmt: skip
    assert by["Files to copy again"]["detail"] == "dashboards/home-charging.yaml"
    assert inv["persons"] == ["Rob", "Suzie"] and inv["zones"][0]["name"] == "Work"
    assert inv["ui_dashboards"] == ["Home charging"] and inv["entities"] == 12  # the disabled one left out
    assert "SECRET" not in json.dumps(inv)  # no password or key from any integration is kept


def test_inventory_says_what_is_wrong() -> None:
    other = dict(RAW, **{".HA_VERSION": "2024.3.1"})
    inv = inventory(other, dict(N.DEFAULTS), {}, {}, {"host": "mqtt.example.com", "port": 8883}, (52.48, -1.9))
    by = {c["what"]: c for c in inv["checks"]}
    assert by["Home Assistant version"]["level"] == "problem"
    assert by["MQTT broker"]["level"] == "check" and "10.0.2.49:1883" in by["MQTT broker"]["detail"]
    assert by["Home"]["level"] == "check" and "km from World Assistant's home" in by["Home"]["detail"]
    bare = inventory({}, dict(N.DEFAULTS), {}, {}, {}, None)
    kinds = {c["what"]: c["level"] for c in bare["checks"]}
    assert kinds["Registry"] == "problem" and kinds["MQTT"] == "problem" and kinds["World Assistant sensors"] == "problem"
    assert bare["names"]["ocpp_charger"] == {"found": None, "candidates": [], "current": "charger"}
