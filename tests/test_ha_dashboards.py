"""The Home Assistant files in homeassistant/ and scripts/ha-setup.py must stay valid, and do the right thing."""

from __future__ import annotations

import importlib.util
import math
import re
import types
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DASHBOARDS = sorted((ROOT / "homeassistant" / "dashboards").glob("*.yaml"))


class _HaLoader(yaml.SafeLoader):
    """Home Assistant's own tags (!secret, !include) are not plain YAML."""


_HaLoader.add_multi_constructor("!", lambda loader, suffix, node: f"<{suffix}>")


def _setup() -> Any:
    spec = importlib.util.spec_from_file_location("ha_setup", ROOT / "scripts" / "ha-setup.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SETUP = _setup()
PEOPLE = ["person.alex", "person.sam"]


def _jinja() -> Any:
    return pytest.importorskip("jinja2")


def test_the_static_dashboards_are_here() -> None:
    assert {p.stem for p in DASHBOARDS} == {"pollen", "fuel", "trains-buses", "roads", "events"}


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.stem)
def test_dashboard_is_valid(path: Path) -> None:
    env = _jinja().Environment()
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["title"] and doc["views"]
    for view in doc["views"]:
        assert view["path"] and view["cards"]
        for card in view["cards"]:
            assert card["type"]
            if card["type"] == "markdown":
                assert "\n" in card["content"], "use a literal block (|): folding breaks tables"
                env.from_string(card["content"])  # raises on a template syntax error


@pytest.mark.parametrize("name", ["pollen", "fuel", "roads", "events"])
def test_location_dashboards_follow_the_viewer(name: str) -> None:
    text = (ROOT / "homeassistant" / "dashboards" / f"{name}.yaml").read_text(encoding="utf-8")
    assert "eq', user" in text, "must pick the viewer's own person, not one fixed person"
    assert "person." not in text.replace("states.person", "")  # nobody's name is written into it


def test_no_secrets_in_the_repo_files() -> None:
    for p in [*DASHBOARDS, ROOT / "scripts" / "ha-setup.py"]:
        low = p.read_text(encoding="utf-8").lower()
        assert "bearer " not in low.replace('"bearer <contents', "").replace("bearer <", "")


# --- the generator ---------------------------------------------------------------------------


def _package(people: list[str] = PEOPLE) -> dict[str, Any]:
    return yaml.load(SETUP.package(people, "192.168.1.10:8010"), Loader=_HaLoader)


def test_package_has_separate_sensors_for_each_person() -> None:
    env = _jinja().Environment()
    doc = _package()
    assert len(doc["rest"]) == 2  # events, for two people
    for r in doc["rest"]:
        assert r["headers"]["Authorization"] == "<secret>"
        assert "sensor.wa_location_" in r["resource_template"]
        assert "zone.home" in r["resource_template"]  # the fallback
        env.from_string(r["resource_template"])
    for who in ("alex", "sam"):
        mine = [r for r in doc["rest"] if f"wa_location_{who}" in r["resource_template"]]
        assert len(mine) == 1
        assert not any(f"wa_location_{other}" in r["resource_template"] for r in mine for other in {"alex", "sam"} - {who})
        ids = {s["unique_id"] for r in mine for s in r["sensor"]}
        assert ids == {f"wa_events_{who}_upcoming", f"wa_events_{who}_next"}
    tmpl = doc["template"][0]["sensor"]
    assert [t["unique_id"] for t in tmpl] == ["wa_location_alex", "wa_location_sam"]
    for t in tmpl:
        env.from_string(t["state"])


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
    env.globals.update(states=st, state_attr=st.attr, distance=_km, as_datetime=__import__("datetime").datetime.fromisoformat, **ctx)
    env.tests["search"] = lambda v, p: re.search(p, v) is not None
    env.tests["in"] = lambda v, c: v in c
    return env


FAR = {"latitude": 52.5, "longitude": -1.9, "gps_accuracy": 20}


def _location(who: str, person: tuple[str, dict[str, Any]] | None) -> tuple[str, str, str]:
    doc = _package()
    sensor = next(t for t in doc["template"][0]["sensor"] if t["unique_id"] == f"wa_location_{who}")
    ents = {"zone.home": ("zoning", {"latitude": 51.5, "longitude": -0.12})}
    if person:
        ents[f"person.{who}"] = person
    env = _env(ents)

    def run(tpl: str) -> str:
        return env.from_string(tpl).render().strip()

    return run(sensor["state"]), run(sensor["attributes"]["latitude"]), run(sensor["attributes"]["longitude"])


def test_each_persons_location_is_their_phone_when_away_else_the_home_zone() -> None:
    assert _location("alex", None) == ("home zone", "51.5", "-0.12")  # no such person yet
    assert _location("alex", ("home", FAR)) == ("home zone", "51.5", "-0.12")  # at home: no GPS jitter
    assert _location("alex", ("Birmingham", FAR)) == ("phone", "52.5", "-1.9")
    assert _location("alex", ("Birmingham", {**FAR, "gps_accuracy": 5000}))[0] == "home zone"  # too vague
    assert _location("sam", ("not_home", {"gps_accuracy": 10}))[0] == "home zone"  # no coordinates


def test_the_person_entity_is_the_one_asked_for() -> None:
    sam = _package()["template"][0]["sensor"][1]  # sam's location sensor
    assert sam["unique_id"] == "wa_location_sam" and "person.sam" in sam["state"]
    assert "person.alex" not in sam["state"]


@pytest.mark.parametrize("bad", ["alex", "person.Alex", "sensor.x", "person.a-b", ""])
def test_generator_rejects_bad_people(bad: str) -> None:
    with pytest.raises(ValueError):
        SETUP.package([bad], "192.168.1.10:8010")


def test_generator_rejects_bad_host_and_duplicates() -> None:
    with pytest.raises(ValueError):
        SETUP.package(["person.alex"], "http://x/y")
    with pytest.raises(ValueError):
        SETUP.package(["person.alex", "person.alex"], "h:1")


def test_generator_writes_the_package(tmp_path: Path) -> None:
    assert SETUP.main(["--host", "nas:8010", "--out", str(tmp_path), *PEOPLE]) == 0
    assert (tmp_path / "world_assistant.yaml").exists() and not (tmp_path / "pollen.yaml").exists()
    assert SETUP.main(["--host", "nas:8010", "--out", str(tmp_path), "nobody"]) == 2


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
    out = lambda who: _env(ents, user=who).from_string(_card("fuel")).render()
    alex, sam = out("Alex"), out("Sam")
    assert "Away BP" in alex and "Home Shell" not in alex and "your location" in alex  # 100 miles away
    assert "Home Shell" in sam and "Away BP" not in sam and "Home" in sam


def test_events_card_reads_only_the_viewers_own_sensor() -> None:
    def ev(name: str) -> dict[str, Any]:
        return {"events": [{"name": name, "date": "2026-10-25", "distance_km": 1.0}], "days": 7}

    ents = {
        "person.alex": ("home", {"friendly_name": "Alex"}),
        "person.sam": ("home", {"friendly_name": "Sam"}),
        "sensor.wa_events_alex_upcoming": ("1", ev("Alex Gig")),
        "sensor.wa_events_sam_upcoming": ("1", ev("Sam Play")),
    }
    card = _card("events")
    a = _env(ents, user="Alex").from_string(card).render()
    s = _env(ents, user="Sam").from_string(card).render()
    assert "Alex Gig" in a and "Sam Play" not in a
    assert "Sam Play" in s and "Alex Gig" not in s
    nobody = _env(ents, user="Guest").from_string(card).render()
    assert "Gig" not in nobody and "Play" not in nobody


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
    assert set(card["entities"]) == want  # regenerate the list if the lattice changes
