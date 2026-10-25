"""The Home Assistant files in homeassistant/ must stay valid, and do the right thing."""

from __future__ import annotations

import math
import re
import types
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DASHBOARDS = sorted((ROOT / "homeassistant" / "dashboards").glob("*.yaml"))
PACKAGES = ROOT / "homeassistant" / "packages"


class _HaLoader(yaml.SafeLoader):
    """Home Assistant's own tags (!secret, !include) are not plain YAML."""


_HaLoader.add_multi_constructor("!", lambda loader, suffix, node: f"<{suffix}>")


def _jinja() -> Any:
    return pytest.importorskip("jinja2")


def test_the_static_dashboards_are_here() -> None:
    assert {p.stem for p in DASHBOARDS} == {"pollen", "fuel", "trains-buses", "roads", "events", "manage", "leaf",
                                            "home-charging"}  # fmt: skip


def _cards(view: dict) -> list[dict]:
    """A view's cards: masonry views list them, sections views keep them in each section."""
    return list(view.get("cards") or []) + [c for sec in view.get("sections") or [] for c in sec.get("cards") or []]


FOOTER_TYPES = {"buttons", "graph"}  # all Home Assistant accepts; anything else is a Configuration error


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.stem)
def test_card_footers_are_a_type_home_assistant_knows(path: Path) -> None:
    """A markdown footer renders as a red "Configuration error" box, so notes go in their own card."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for view in doc["views"]:
        for card in _cards(view):
            for c in [card, *(card.get("cards") or []), *( [card["card"]] if card.get("card") else [])]:
                foot = c.get("footer") if isinstance(c, dict) else None
                if foot:
                    assert foot.get("type") in FOOTER_TYPES, f"{c.get('title')}: footer {foot.get('type')!r}"


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.stem)
def test_dashboard_is_valid(path: Path) -> None:
    env = _jinja().Environment()
    for name in ("as_local", "from_json", "to_json", "as_datetime", "as_timestamp", "timestamp_custom"):  # Home Assistant's
        env.filters.setdefault(name, lambda v, *a: v)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["title"] and doc["views"]
    for view in doc["views"]:
        assert view["path"] and _cards(view)
        for card in _cards(view):
            assert card["type"]
            if card["type"] == "markdown":
                assert "\n" in card["content"], "use a literal block (|): folding breaks tables"
                env.from_string(card["content"])  # raises on a template syntax error


@pytest.mark.parametrize("name", ["pollen", "fuel", "roads"])
def test_location_dashboards_follow_the_viewer(name: str) -> None:
    text = (ROOT / "homeassistant" / "dashboards" / f"{name}.yaml").read_text(encoding="utf-8")
    assert "eq', user" in text, "must pick the viewer's own person, not one fixed person"
    assert "person." not in text.replace("states.person", "")  # nobody's name is written into it


def test_no_secrets_in_the_repo_files() -> None:
    for p in [*DASHBOARDS, *PACKAGES.glob("*.yaml")]:
        low = p.read_text(encoding="utf-8").lower()
        assert "bearer " not in low.replace('"bearer <contents', "").replace("bearer <", "")


class _Attrs(dict):  # type: ignore[type-arg]
    def __getattr__(self, name: str) -> Any:
        if name in self:
            return self[name]
        raise AttributeError(name)


class _States:
    """Just enough of Home Assistant's template namespace."""

    def __init__(self, entities: dict[str, tuple[str, dict[str, Any]]]) -> None:
        self.e = {
            k: types.SimpleNamespace(entity_id=k, state=v[0], attributes=_Attrs(v[1]), name=v[1].get("friendly_name", k))
            for k, v in entities.items()
        }

    def __call__(self, entity_id: str) -> str:
        return self.e[entity_id].state if entity_id in self.e else "unknown"

    def __getitem__(self, entity_id: str) -> Any:
        return self.e.get(entity_id)

    @property
    def person(self) -> list[Any]:
        return [v for k, v in self.e.items() if k.startswith("person.")]

    @property
    def zone(self) -> Any:
        return types.SimpleNamespace(home=self.e.get("zone.home"))

    @property
    def sensor(self) -> list[Any]:
        return [v for k, v in self.e.items() if k.startswith("sensor.")]

    def attr(self, entity_id: str, name: str) -> Any:
        e = self.e.get(entity_id)
        return e.attributes.get(name) if e else None


def _km(a: float, b: float, c: float, d: float) -> float:
    p = math.pi / 180
    h = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def _env(ents: dict[str, tuple[str, dict[str, Any]]], **ctx: Any) -> Any:
    jinja2 = _jinja()
    st = _States(ents)
    env = jinja2.Environment()
    env.globals.update(states=st, state_attr=st.attr, distance=_km,
                       is_state=lambda e, v: st(e) == v, as_datetime=__import__("datetime").datetime.fromisoformat, **ctx)
    env.tests["search"] = lambda v, p: re.search(p, v) is not None
    env.tests["in"] = lambda v, c: v in c
    return env


FAR = {"latitude": 52.5, "longitude": -1.9, "gps_accuracy": 20}


# --- what each viewer sees --------------------------------------------------------------------


def _card(name: str, index: int = 0) -> str:
    doc = yaml.safe_load((ROOT / "homeassistant" / "dashboards" / f"{name}.yaml").read_text(encoding="utf-8"))
    return doc["views"][0]["cards"][index]["content"]  # type: ignore[no-any-return]


def test_fuel_card_shows_each_viewer_prices_where_that_viewer_is() -> None:
    near_home = {"name": "Home Shell", "brand": "Shell", "postcode": "H1", "lat": 51.51, "lon": -0.12, "pence": 130.0}
    near_alex = {"name": "Away BP", "brand": "BP", "postcode": "A1", "lat": 52.5, "lon": -1.9, "pence": 128.0}
    ents = {
        "zone.home": ("zoning", {"latitude": 51.5, "longitude": -0.12, "friendly_name": "Home"}),
        "person.alex": ("Birmingham", {**FAR, "friendly_name": "Alex"}),
        "person.sam": ("home", {"friendly_name": "Sam", "latitude": 51.5, "longitude": -0.12}),
        "sensor.wa_fuel_tile_e10_1_m1": ("2", {"stations": [near_home, near_alex]}),
    }
    out = lambda who: _env(ents, user=who).from_string(_card("fuel", 2)).render()
    alex, sam = out("Alex"), out("Sam")
    assert "Away BP" in alex and "Home Shell" not in alex and "your location" in alex  # 100 miles away
    assert "Home Shell" in sam and "Away BP" not in sam and "Home" in sam
    assert "petrol" in sam and "diesel" not in sam


def test_fuel_card_follows_the_fuel_dropdown() -> None:
    stn = {"name": "Diesel Stop", "brand": "X", "postcode": "D1", "lat": 51.51, "lon": -0.12, "pence": 140.0}
    ents = {
        "zone.home": ("zoning", {"latitude": 51.5, "longitude": -0.12, "friendly_name": "Home"}),
        "sensor.wa_fuel_tile_b7_standard_1_m1": ("1", {"stations": [stn]}),
        "input_select.wa_fuel_type": ("Diesel", {}),
    }
    card = _card("fuel", 2)
    assert "Diesel Stop" in _env(ents, user="Anyone").from_string(card).render()
    ents["input_select.wa_fuel_type"] = ("Petrol (E10)", {})
    assert "Diesel Stop" not in _env(ents, user="Anyone").from_string(card).render()


def test_fuel_package_offers_petrol_and_diesel() -> None:
    doc = yaml.safe_load((PACKAGES / "world_assistant_fuel.yaml").read_text(encoding="utf-8"))
    assert doc["input_select"]["wa_fuel_type"]["options"] == ["Petrol (E10)", "Diesel"]


def test_events_package_asks_for_the_home_zone_and_keeps_the_token_secret() -> None:
    env = _jinja().Environment()
    doc = yaml.load((PACKAGES / "world_assistant_events.yaml").read_text(encoding="utf-8"), Loader=_HaLoader)
    (r,) = doc["rest"]
    assert r["resource"] == "<secret>" and r["headers"]["Authorization"] == "<secret>"
    assert "zone.home" in r["params"]["lat"] and "zone.home" in r["params"]["lon"]
    for v in r["params"].values():
        env.from_string(v)
    assert {s["unique_id"] for s in r["sensor"]} == {"wa_events_home_upcoming", "wa_events_home_next"}


def test_events_card_lists_the_events_near_home() -> None:
    ev = {"events": [{"name": "Home Gig", "date": "2026-10-25", "distance_km": 1.0}], "days": 7, "radius_km": 8}
    ents = {"zone.home": ("zoning", {"latitude": 51.5, "longitude": -0.12, "friendly_name": "Home"}),
            "sensor.wa_events_home_upcoming": ("1", ev)}  # fmt: skip
    out = _env(ents, user="Anyone").from_string(_card("events")).render()
    assert "Home Gig" in out and "of Home" in out
    assert "Nothing listed" in _env({}, user="Anyone").from_string(_card("events")).render()


def test_event_names_link_to_their_page_when_there_is_one() -> None:
    evs = [{"name": "Linked [live]", "date": "2026-10-25", "distance_km": 1.0, "url": "https://example.com/e/1"},
           {"name": "Plain Gig", "date": "2026-10-26", "distance_km": 2.0, "url": None}]  # fmt: skip
    ents = {"sensor.wa_events_home_upcoming": ("2", {"events": evs, "days": 7, "radius_km": 8}),
            "sensor.wa_events_place_work": ("1", {"friendly_name": "Events near Work", "place": "Work", "events": evs[:1] + [{**evs[1], "url": "https://x.test/2"}]})}  # fmt: skip
    for card in (0, 1):
        out = _env(ents, user="Anyone").from_string(_card("events", card)).render()
        assert "[**Linked (live)**](https://example.com/e/1)" in out  # brackets in a name cannot break the link
        assert "**Plain Gig**" in out or "[**Plain Gig**](https://x.test/2)" in out


# --- pollen --------------------------------------------------------------------------------------

POLLEN_PACKAGE = ROOT / "homeassistant" / "packages" / "world_assistant_pollen.yaml"


def _point(lat: float, lon: float, risk: float, label: str, birch: int) -> dict[str, Any]:
    return {"lat": lat, "lon": lon, "dates": ["2026-10-21", "2026-10-22"], "risk": [risk, risk], "label": [label, label],
            "allergens": [{"name": "Birch", "levels": [birch, birch]}, {"name": "Grasses", "levels": [0, 1]}]}  # fmt: skip


POLLEN_ENTS = {
    "zone.home": ("zoning", {"latitude": 51.5, "longitude": -0.12, "friendly_name": "Home"}),
    "person.alex": ("Birmingham", {**FAR, "friendly_name": "Alex"}),
    "person.sam": ("home", {"friendly_name": "Sam", "latitude": 51.5, "longitude": -0.12}),
    "sensor.wa_pollen_tile_25_m1": ("1", {"points": [_point(51.5, -0.5, 3.0, "low", 1)]}),  # near home
    "sensor.wa_pollen_tile_26_m1": ("1", {"points": [_point(52.5, -1.5, 8.0, "very high", 4)]}),  # near Alex
}


def test_home_pollen_sensors_read_the_nearest_point_to_the_home_zone() -> None:
    doc = yaml.load(POLLEN_PACKAGE.read_text(encoding="utf-8"), Loader=_HaLoader)
    sensors = {t["unique_id"]: t for t in doc["template"][0]["sensor"]}
    assert set(sensors) == {"wa_pollen_home_allergy_risk", *(f"wa_pollen_home_{n}" for n in ("alder", "birch", "grasses", "mugwort", "olive", "ragweed"))}
    env = _env(POLLEN_ENTS)

    def run(uid: str) -> str:
        return env.from_string(sensors[uid]["state"]).render().strip()

    assert run("wa_pollen_home_allergy_risk") == "3.0"  # the point near home, not the one near Alex
    assert run("wa_pollen_home_birch") == "1" and run("wa_pollen_home_grasses") == "0"
    assert run("wa_pollen_home_alder") == "unknown"  # not in the forecast
    far = _env({"zone.home": POLLEN_ENTS["zone.home"], "sensor.wa_pollen_tile_x": ("1", {"points": [_point(57.0, -4.0, 9, "high", 3)]})})
    assert far.from_string(sensors["wa_pollen_home_allergy_risk"]["state"]).render().strip() == "unknown"  # too far to trust
    # the dashboard graphs only sensors this package defines
    dash = yaml.safe_load((ROOT / "homeassistant" / "dashboards" / "pollen.yaml").read_text(encoding="utf-8"))
    graphed = {e["entity"] for c in dash["views"][0]["cards"] if c["type"] == "history-graph" for e in c["entities"]}
    assert graphed and {g.removeprefix("sensor.") for g in graphed} <= set(sensors)
    assert any(c["type"] == "picture-entity" and c["entity"] == "image.wa_pollen_map" for c in dash["views"][0]["cards"])


def test_pollen_card_shows_each_viewer_the_forecast_nearest_them() -> None:
    card = _card("pollen", 2)
    alex = _env(POLLEN_ENTS, user="Alex").from_string(card).render()
    sam = _env(POLLEN_ENTS, user="Sam").from_string(card).render()
    assert "very high" in alex and "your location" in alex and "low" not in alex.split("**")[1]
    assert "**low**" in sam and "**very high**" not in sam
    assert "Birch" in sam and "🟩" in sam


def test_pollen_map_card_lists_every_lattice_point() -> None:
    from pathlib import Path

    import yaml

    from app.ha_publish import pollen_point_key
    from app.pollen_grid import grid_points

    cfg = yaml.safe_load(Path("homeassistant/dashboards/pollen.yaml").read_text())
    card = next(c for v in cfg["views"] for c in v["cards"] if c["type"] == "map")
    want = {f"sensor.wa_pollen_{pollen_point_key(a, b)}" for a, b in grid_points(1.0)}
    assert {e["entity"] for e in card["entities"]} == want  # regenerate the list if the lattice changes
    # each marker shows its allergy risk, not the initials of its name, and none are grouped away
    assert all(e["label_mode"] == "state" for e in card["entities"]) and card["cluster"] is False


def test_events_card_lists_whatever_places_exist() -> None:
    def place(name: str, ev: str) -> tuple[str, dict[str, Any]]:
        return ("1", {"friendly_name": f"Events near {name}", "place": name, "days": 7, "radius_km": 8,
                      "expires_at": "2026-11-20T00:00:00+00:00",
                      "events": [{"name": ev, "date": "2026-10-25", "distance_km": 1.0}]})  # fmt: skip

    ents = {
        "sensor.wa_events_place_mum_s_house": place("Mum's house", "Mum Gig"),
        "sensor.wa_events_place_work": place("Work", "Work Play"),
        "sensor.wa_events_locations": ("2", {"max": 8, "ttl_days": 30, "provider_calls_per_day": 32}),
    }
    out = _env(ents, user="Anyone").from_string(_card("events", 1)).render()
    assert "Mum's house" in out and "Mum Gig" in out and "Work Play" in out
    assert "Watching 2 of 8" in out
    none = _env({}, user="Anyone").from_string(_card("events", 1)).render()
    assert "No other places yet" in none


def test_events_package_commands_render_valid_json() -> None:
    import json

    env = _jinja().Environment()
    doc = yaml.load((PACKAGES / "world_assistant_events.yaml").read_text(encoding="utf-8"), Loader=_HaLoader)
    add = doc["rest_command"]["wa_add_location"]
    body = json.loads(env.from_string(add["payload"]).render(name='Mum\'s "house"', lat=52.4, lon=-1.9))
    assert body == {"name": 'Mum\'s "house"', "lat": 52.4, "lon": -1.9}
    body = json.loads(env.from_string(add["payload"]).render(name="x", lat=1, lon=2, radius_km=12))
    assert body["radius_km"] == 12
    rem = doc["rest_command"]["wa_remove_location"]
    assert json.loads(env.from_string(rem["payload"]).render(name="x")) == {"name": "x"}
    assert add["headers"]["Authorization"] == "<secret>" and rem["url"] == "<secret>"


def test_bus_card_shows_next_bus_chance_and_record() -> None:
    ents = {
        "sensor.wa_bus_main_summary": ("The last bus got there at 07:52.", {"friendly_name": "Bus tracker main Target summary"}),
        "sensor.wa_bus_main_last_bus": ("07:52", {}),
        "sensor.wa_bus_main_next_board": ("4", {"friendly_name": "Next bus: Worcester Park", "next": [
            {"minutes": 4, "towards": "Kingston"}, {"minutes": 11, "towards": "Kingston"}]}),
        "sensor.wa_bus_main_chance": ("80", {"deadline": "08:10", "bus": "07:46", "based_on_days": 9, "based_on": "this departure",
                                              "later_buses": [{"slot": "07:55", "chance": 60}], "relevant": True, "days_recorded": 9}),
        "sensor.wa_bus_main_record": ("75", {"window_days": 30, "success": 15, "failure": 5,
                                              "recent_days": [{"date": "2026-10-21", "buses": ["07:11 success (07:25)", "07:46 failure"]}]}),
    }  # fmt: skip
    card = next(c for c in yaml.safe_load((ROOT / "homeassistant" / "dashboards" / "trains-buses.yaml").read_text(encoding="utf-8"))["views"][0]["cards"] if c.get("title") == "Bus tracker")
    out = _env(ents, user="Anyone").from_string(card["content"]).render()
    assert "Next bus at Worcester Park" in out and "4 min, then 11" in out and "towards Kingston" in out
    assert "80% for the 07:46 bus" in out and "07:55: 60%" in out
    assert "75% made it (15 reached it, 5 did not)" in out and "07:46 failure" in out
    ents["sensor.wa_bus_main_ended_early"] = ("1", {"today": ["07:41 turned back at Malden Road (4 stops short), next seen at Worcester Park 07:55"],
                                                    "recent_days": [{"date": "2026-10-20", "count": 2, "buses": ["07:30 stopped reporting at X (3 stops short)"]}]})
    import datetime

    env = _env(ents, user="Anyone")
    env.globals["now"] = lambda: datetime.datetime(2026, 10, 21, 7, 0, tzinfo=datetime.UTC)
    early = env.from_string(card["content"]).render()
    assert "Buses that ended early today**: 1" in early and "turned back at Malden Road" in early
    assert "2026-10-20: 2; 07:30 stopped reporting at X" in early
    ents["sensor.wa_bus_main_chance"] = ("unknown", {"relevant": False, "reason": "after 08:00", "deadline": "08:10"})
    env = _env(ents, user="Anyone")
    env.globals["now"] = lambda: datetime.datetime(2026, 10, 21, 7, 0, tzinfo=datetime.UTC)
    quiet = env.from_string(card["content"]).render()
    assert "not shown (after 08:00)" in quiet and "80%" not in quiet


def test_bus_package_sends_only_the_time_that_changed() -> None:
    env = _jinja().Environment()
    doc = yaml.load((PACKAGES / "world_assistant_bus.yaml").read_text(encoding="utf-8"), Loader=_HaLoader)
    assert set(doc["input_datetime"]) == {"wa_bus_deadline", "wa_bus_earliest", "wa_bus_show_until"}
    cmd = doc["rest_command"]["wa_bus_settings"]
    assert cmd["url"] == "<secret>" and cmd["headers"]["Authorization"] == "<secret>"
    import json

    body = json.loads(env.from_string(cmd["payload"]).render(deadline="08:30", earliest="", show_until=""))
    assert body == {"deadline": "08:30", "earliest": "", "show_until": ""}  # the server ignores blanks
    action = next(a for a in doc["automation"][0]["actions"] if "action" in a and a["action"] == "rest_command.wa_bus_settings")
    for v in action["data"].values():
        env.from_string(v)


def _script_vars(name: str, ents: dict[str, tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Render a manage-package script's `variables` step in order, as Home Assistant would."""
    doc = yaml.load((PACKAGES / "world_assistant_manage.yaml").read_text(encoding="utf-8"), Loader=_HaLoader)
    step = next(x for x in doc["script"][name]["sequence"] if "variables" in x)
    env = _env(ents)
    out: dict[str, Any] = {}
    for key, template in step["variables"].items():
        out[key] = env.from_string(template).render(**out)
    return out


ZONES = {
    "zone.work": ("zoning", {"latitude": 51.5, "longitude": -0.12, "friendly_name": "Work"}),
    "zone.school": ("zoning", {"latitude": 51.4, "longitude": -0.2, "friendly_name": "School"}),
}


def test_manage_scripts_read_a_zone_or_a_position() -> None:
    ents = {**ZONES, "input_text.wa_place": ("zone.work", {}), "input_text.wa_place_name": ("", {})}
    v = _script_vars("wa_add_location", ents)
    assert (float(v["lat"]), float(v["lon"]), v["place_name"]) == (51.5, -0.12, "Work")  # name comes from the zone
    ents["input_text.wa_place"] = ("52.48, -1.9", {})
    ents["input_text.wa_place_name"] = ("Mum's house", {})
    v = _script_vars("wa_add_location", ents)
    assert (float(v["lat"]), float(v["lon"]), v["place_name"]) == (52.48, -1.9, "Mum's house")
    ents["input_text.wa_place"] = ("nonsense", {})
    v = _script_vars("wa_add_location", ents)
    assert float(v["lat"]) == 0  # the script then tells the user it could not read a position


def test_manage_route_script_reads_both_ends() -> None:
    ents = {**ZONES, "input_text.wa_route_from": ("zone.work", {}), "input_text.wa_route_to": ("51.37, -0.25", {})}
    v = _script_vars("wa_add_route", ents)
    assert (float(v["from_lat"]), float(v["to_lat"]), float(v["to_lon"])) == (51.5, 51.37, -0.25)
    assert v["from_name"] == "Work" and v["to_name"] == "51.37, -0.25"
    ents["input_text.wa_route_to"] = ("", {})
    assert float(_script_vars("wa_add_route", ents)["to_lat"]) == 0


def test_manage_rest_commands_send_valid_json_even_for_awkward_names() -> None:
    import json

    env = _jinja().Environment()
    env.policies["json.dumps_function"] = json.dumps
    doc = yaml.load((PACKAGES / "world_assistant_manage.yaml").read_text(encoding="utf-8"), Loader=_HaLoader)
    cmd = doc["rest_command"]["wa_add_route"]
    assert cmd["url"] == "<secret>" and cmd["headers"]["Authorization"] == "<secret>"
    body = json.loads(env.from_string(cmd["payload"]).render(
        from_name='Mum\'s "house"', from_lat=51.5, from_lon=-0.1, to_name="Work", to_lat=51.4, to_lon=-0.2))  # fmt: skip
    assert body["from_name"] == 'Mum\'s "house"' and body["to_lat"] == 51.4
    gone = json.loads(env.from_string(doc["rest_command"]["wa_remove_route"]["payload"]).render(from_name="A", to_name="B"))
    assert gone == {"from_name": "A", "to_name": "B"}


def test_manage_dashboard_lists_what_is_set_up() -> None:
    ents = {
        "sensor.wa_events_place_mum_s_house": ("3", {"friendly_name": "Mum's house", "place": "Mum's house"}),
        "sensor.wa_roads_corridor_trip_ha_home_to_work": ("1", {"friendly_name": "Home to Work: active works"}),
        "sensor.wa_events_locations": ("1", {"max": 8}),
    }
    card = next(c for c in yaml.safe_load((ROOT / "homeassistant" / "dashboards" / "manage.yaml").read_text(encoding="utf-8"))["views"][0]["cards"] if c["type"] == "markdown")
    out = _env(ents, user="Anyone").from_string(card["content"]).render()
    assert "Mum's house: 3 events" in out and "Home to Work: 1 active works" in out and "Watching 1 of 8 places" in out
    empty = _env({}, user="Anyone").from_string(card["content"]).render()
    assert empty.count("- none") == 2


# --- the Leaf -----------------------------------------------------------------------------------

LEAF_OK = {
    "sensor.hv66bzd_instrument_soc_nissan_leaf": ("81.0757", {}),
    "sensor.wa_car_range_miles": ("111.7", {}),
    "sensor.nissan_leaf_hv66bzd_charge_substate": ("onrequest", {}),
    "binary_sensor.nissan_leaf_hv66bzd_charging_status": ("off", {}),
    "binary_sensor.nissan_leaf_hv66bzd_charge_port": ("off", {}),
    "binary_sensor.hv66bzd_12v_battery_voltage_alert": ("off", {}),
    "binary_sensor.hv66bzd_12v_battery_charging": ("off", {}),
    "sensor.hv66bzd_12v_battery_voltage": ("12.4", {}),
    "lock.hv66bzd_vehicle_lock": ("locked", {}),
    **{f"binary_sensor.hv66bzd_{d}": ("off", {}) for d in
       ("front_left_door", "front_right_door", "rear_left_door", "rear_right_door", "trunk")},
    "binary_sensor.nissan_leaf_hv66bzd_cell_voltage_alerts": ("off", {}),
    "binary_sensor.nissan_leaf_hv66bzd_cell_temperature_alerts": ("off", {}),
    **{f"sensor.leaf_tyre_pressure_{t}_psi": ("36.0", {}) for t in
       ("front_left", "front_right", "rear_left", "rear_right")},
}


def _leaf(**over: tuple[str, dict[str, Any]]) -> str:
    return _env({**LEAF_OK, **over}).from_string(_card("leaf", 1)).render()


def test_leaf_says_nothing_to_do_when_all_is_well() -> None:
    out = _leaf()
    assert "Nothing to do." in out and "81%" in out and "111.7 miles" in out
    assert "🔌" not in out and "⚠️" not in out and "🛞" not in out


def test_leaf_reports_charging_when_it_is() -> None:
    out = _leaf(**{
        "binary_sensor.nissan_leaf_hv66bzd_charging_status": ("on", {}),
        "binary_sensor.nissan_leaf_hv66bzd_charge_port": ("on", {}),
        "sensor.nissan_leaf_hv66bzd_time_to_full_charge": ("3h 51m", {}),
    })
    assert "Charging, 3h 51m to full." in out and "🔌" not in out  # plugged in and charging: no warning


def test_leaf_flags_what_needs_doing() -> None:
    out = _leaf(**{
        "binary_sensor.nissan_leaf_hv66bzd_charge_port": ("on", {}),  # plugged in, not charging
        "sensor.hv66bzd_instrument_soc_nissan_leaf": ("7", {}),
        "sensor.wa_car_range_miles": ("7.7", {}),
        "sensor.hv66bzd_12v_battery_voltage": ("11.6", {}),
        "lock.hv66bzd_vehicle_lock": ("unlocked", {}),
        "binary_sensor.hv66bzd_trunk": ("on", {}),
        "sensor.leaf_tyre_pressure_front_right_psi": ("32.5", {}),
        "sensor.leaf_tyre_pressure_rear_left_psi": ("41.0", {}),
    })
    assert "Plugged in but not charging (onrequest)" in out
    assert "Charge is 7%, about 7.7 miles" in out
    assert "12V battery at 11.6V" in out
    assert "🔓 Unlocked" in out and "🚪 Boot open" in out
    assert "Front right tyre 32.5 psi" in out and "Rear left tyre 41.0 psi" in out
    assert "Front left" not in out and "Rear right" not in out  # the two at 36 psi say nothing
    assert "Nothing to do." not in out


def test_leaf_is_quiet_when_a_sensor_is_missing() -> None:
    """A car asleep or an entity not yet created must not produce a false warning."""
    out = _env({}).from_string(_card("leaf", 1)).render()
    assert "Nothing to do." in out and "⚠️" not in out and "🛞" not in out


def test_home_charging_plan_table_renders() -> None:
    """The plan card as Home Assistant shows it: a row for each of the six slots, and the health max suggestion."""
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo

    doc = yaml.safe_load((ROOT / "homeassistant" / "dashboards" / "home-charging.yaml").read_text(encoding="utf-8"))
    cards = [c for v in doc["views"] for c in _cards(v) if c["type"] == "markdown"]
    env = _jinja().Environment()
    env.filters |= {"as_local": lambda d: d.astimezone(ZoneInfo("Europe/London")), "from_json": json.loads,
                    "int": lambda v, d=0: int(float(v)) if str(v).replace(".", "", 1).isdigit() else d}  # fmt: skip
    starts = ["00:00", "04:00", "07:00", "13:00", "16:00", "22:00"]
    targets = [{"n": n, "start": st, "charge": n % 2 == 0, "covers": "x-y", "wanted": 20 if n % 2 else 50, "target": 20 if n % 2 else 50,
                "over": False} for n, st in enumerate(starts, 1)]  # fmt: skip
    targets[3] |= {"wanted": 95, "target": 80, "over": True, "covers": "16:00-22:00"}
    st = {"sensor.wa_charge_targets#slots": targets, "sensor.wa_energy_charge_schedule": "04:00-07:00, 13:00-16:00, 22:00-00:00",
          "sensor.wa_energy_charge_schedule#tariff": "E-1R-COSY-22-12-08-C", "input_number.wa_max_target_pct": "80",
          "input_number.wa_floor_pct": "20.0", "sensor.wa_inverter_slot_check": "ok", "sensor.wa_battery_charge_rate": "4.8",
          "sensor.energy_production_tomorrow": "7.8", "sensor.energy_production_today": "3.3",
          "input_text.wa_free_restore": json.dumps({"slot": 4, "cap": 70, "on": "on", "end": "2026-10-25T14:00:00+00:00"}),
          "sensor.wa_energy_free_electricity_next_start#upcoming": [
              {"start": "2026-10-25T13:00:00+00:00", "end": "2026-10-25T14:00:00+00:00", "joined": True},
              {"start": "2026-10-25T14:00:00+00:00", "end": "2026-10-25T15:00:00+00:00", "joined": False}]}  # fmt: skip
    st |= {f"number.office_inverter_time_slot_{n}_capacity": f"{v}.0" for n, v in enumerate([10, 80, 10, 70, 10, 80], 1)}
    st |= {f"text.office_inverter_time_slot_{n}_start": t for n, t in enumerate(starts, 1)} | {"text.office_inverter_time_slot_6_start": "23:00"}
    funcs = {"states": lambda e: str(st.get(e, "unknown")), "is_state": lambda e, v: st.get(e) == v,
             "state_attr": lambda e, a: st.get(f"{e}#{a}"), "as_datetime": datetime.fromisoformat}  # fmt: skip
    free = env.from_string(next(c for c in cards if "upcoming" in c["content"])["content"]).render(**funcs)
    plan = env.from_string(next(c for c in cards if "wa_charge_targets" in c["content"])["content"]).render(**funcs)
    assert "- Sun 13:00-14:00: ✅ joined" in free and "- Sun 14:00-15:00: not joined" in free
    assert "Charging free now**: slot 4 until 14:00, then put back to 70%." in free
    lines = [x for x in plan.splitlines() if x.startswith("|")]
    assert len(lines) == 8 and lines[5] == "| 4 | 13:00 | on | 16:00-22:00 | 95% | **80%** ⚠️ | 70% |"
    assert lines[0].startswith("| Slot | From | Grid charge | Covers | Needed |")
    assert lines[2] == "| 1 | 00:00 | off | discharge to the floor | 20% | **20%** | 10% |"
    assert lines[7] == "| 6 | 22:00 | on | x-y | 50% | **50%** | 80% (starts 23:00) |"
    assert "Suggest raising the health max**: slot 4 (13:00) wants 95%" in plan and "**E-1R-COSY-22-12-08-C**" in plan
    assert "Charging from the grid at 4.8 kW (12% an hour)" in plan


def test_files_name_package_sensors_by_the_id_home_assistant_gives_them() -> None:
    """Home Assistant makes a template entity's id from its name, not its unique_id ("WA car charging plan" is
    sensor.wa_car_charging_plan, whatever its unique_id). A file naming sensor.<unique_id> finds nothing."""
    import re

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_multi_constructor("!", lambda loader, suffix, node: None)
    wrong: dict[str, str] = {}
    files = sorted((ROOT / "homeassistant").rglob("*.yaml"))
    for f in files:
        if "packages" not in f.parts:
            continue
        for block in (yaml.load(f.read_text(encoding="utf-8"), Loader=Loader) or {}).get("template") or []:
            for domain, items in block.items():
                for x in items if isinstance(items, list) else []:
                    slug = re.sub(r"[^a-z0-9]+", "_", str(x.get("name", "")).lower()).strip("_")
                    if x.get("unique_id") and slug != x["unique_id"]:
                        wrong[f"{domain}.{x['unique_id']}"] = f"{domain}.{slug}"
    assert "sensor.wa_ev_plan" in wrong  # the case that broke the car plan's triggers
    for f in files:
        text = f.read_text(encoding="utf-8")
        for bad, good in wrong.items():
            assert not re.search(re.escape(bad) + r"(?![a-z0-9_])", text), f"{f.name} names {bad}; Home Assistant calls it {good}"
