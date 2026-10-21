"""The Home Assistant dashboards in homeassistant/dashboards must stay valid YAML with valid templates."""

from __future__ import annotations

from pathlib import Path

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
        assert "zone.home" in r["resource_template"]  # the point comes from Home Assistant, not World Assistant
        assert r["headers"]["Authorization"] == "<secret>"  # the token lives in secrets.yaml, never here
        env.from_string(r["resource_template"])
    for t in doc["template"][0]["sensor"]:
        env.from_string(t["state"])
    assert "Bearer " not in text.replace('"Bearer <contents', "")
    ids = {t["unique_id"] for t in doc["template"][0]["sensor"]}
    assert ids == {f"wa_pollen_{n}" for n in ("alder", "birch", "grasses", "mugwort", "olive", "ragweed")}
    # every entity the pollen dashboard graphs is defined here
    dash = yaml.safe_load((DASHBOARDS[0].parent / "pollen.yaml").read_text(encoding="utf-8"))
    graphed = {e["entity"] for c in dash["views"][0]["cards"] if c["type"] == "history-graph" for e in c["entities"]}
    assert {g.removeprefix("sensor.") for g in graphed} <= ids | {"wa_pollen_allergy_risk"}
