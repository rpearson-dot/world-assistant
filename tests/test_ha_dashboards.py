"""The Home Assistant dashboards in homeassistant/dashboards must stay valid YAML with valid templates."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest
import yaml


class _HaLoader(yaml.SafeLoader):
    """Home Assistant's own tags (!secret, !include) are not plain YAML."""


_HaLoader.add_multi_constructor("!", lambda loader, suffix, node: f"<{suffix}>")

PACKAGE = Path(__file__).resolve().parent.parent / "homeassistant" / "packages" / "world_assistant.yaml"
DASHBOARDS = sorted((Path(__file__).resolve().parent.parent / "homeassistant" / "dashboards").glob("*.yaml"))


def test_there_is_one_dashboard_per_data_source() -> None:
    assert {p.stem for p in DASHBOARDS} == {"pollen", "fuel", "trains-buses", "roads", "events"}
    assert PACKAGE.exists()


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.stem)
def test_dashboard_is_valid(path: Path) -> None:
    jinja2 = pytest.importorskip("jinja2")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["title"] and doc["views"]
    env = jinja2.Environment()
    for view in doc["views"]:
        assert view["path"] and view["cards"]
        for card in view["cards"]:
            assert card["type"]
            if card["type"] == "markdown":
                assert "\n" in card["content"], "use a literal block (|): folding breaks tables"
                env.from_string(card["content"])  # raises on a template syntax error


def test_dashboards_hold_no_real_coordinates_or_secrets() -> None:
    for p in DASHBOARDS:
        text = p.read_text(encoding="utf-8")
        assert "latitude" not in text.replace("z.attributes.latitude", "")
        assert "token" not in text.lower()


def test_package_is_valid_and_follows_the_home_zone() -> None:
    jinja2 = pytest.importorskip("jinja2")
    text = PACKAGE.read_text(encoding="utf-8")
    doc = yaml.load(text, Loader=_HaLoader)
    env = jinja2.Environment()
    for r in doc["rest"]:
        # the point comes from Home Assistant (its app's location, else the home zone), never from World Assistant
        assert "sensor.wa_location" in r["resource_template"] and "zone.home" in r["resource_template"]
        assert r["headers"]["Authorization"] == "<secret>"  # the token lives in secrets.yaml, never here
        env.from_string(r["resource_template"])
    for t in doc["template"][0]["sensor"]:
        env.from_string(t["state"])
    assert "Bearer " not in text.replace('"Bearer <contents', "")
    ids = {t["unique_id"] for t in doc["template"][0]["sensor"]} - {"wa_location"}
    assert ids == {f"wa_pollen_{n}" for n in ("alder", "birch", "grasses", "mugwort", "olive", "ragweed")}
    # every entity the pollen dashboard graphs is defined here
    dash = yaml.safe_load((DASHBOARDS[0].parent / "pollen.yaml").read_text(encoding="utf-8"))
    graphed = {e["entity"] for c in dash["views"][0]["cards"] if c["type"] == "history-graph" for e in c["entities"]}
    assert {g.removeprefix("sensor.") for g in graphed} <= ids | {"wa_pollen_allergy_risk"}


class _States:
    """Just enough of Home Assistant's template namespace to run the location sensor."""

    def __init__(self, entities: dict[str, tuple[str, dict[str, Any]]]) -> None:
        self.e = {k: types.SimpleNamespace(state=v[0], attributes=_Attrs(v[1])) for k, v in entities.items()}

    def __getitem__(self, entity_id: str) -> Any:
        return self.e.get(entity_id)

    def attr(self, entity_id: str, name: str) -> Any:
        e = self.e.get(entity_id)
        return e.attributes.get(name) if e else None


class _Attrs(dict):  # type: ignore[type-arg]
    def __getattr__(self, name: str) -> Any:
        if name in self:
            return self[name]
        raise AttributeError(name)


def _location(person: tuple[str, dict[str, Any]] | None) -> tuple[str, Any, Any]:
    jinja2 = pytest.importorskip("jinja2")
    doc = yaml.load(PACKAGE.read_text(encoding="utf-8"), Loader=_HaLoader)
    sensor = next(t for t in doc["template"][0]["sensor"] if t["unique_id"] == "wa_location")
    ents = {"zone.home": ("zoning", {"latitude": 51.5, "longitude": -0.12})}
    if person:
        ents["person.CHANGE_ME"] = person
    st = _States(ents)
    env = jinja2.Environment()
    env.globals.update(states=st, state_attr=st.attr)

    def run(tpl: str) -> str:
        return env.from_string(tpl).render().strip()

    return (run(sensor["state"]), run(sensor["attributes"]["latitude"]), run(sensor["attributes"]["longitude"]))


def test_location_is_the_phone_when_away_else_the_home_zone() -> None:
    far = {"latitude": 52.0, "longitude": -1.0, "gps_accuracy": 20}
    assert _location(None) == ("home zone", "51.5", "-0.12")  # no person set up yet
    assert _location(("home", far)) == ("home zone", "51.5", "-0.12")  # at home: stable, no GPS jitter
    assert _location(("Birmingham", far)) == ("phone", "52.0", "-1.0")
    assert _location(("Birmingham", {**far, "gps_accuracy": 5000}))[0] == "home zone"  # too vague to trust
    assert _location(("not_home", {"gps_accuracy": 10}))[0] == "home zone"  # no coordinates reported
