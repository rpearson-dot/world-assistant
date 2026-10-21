"""The Home Assistant dashboards in homeassistant/dashboards must stay valid YAML with valid templates."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

DASHBOARDS = sorted((Path(__file__).resolve().parent.parent / "homeassistant" / "dashboards").glob("*.yaml"))


def test_there_is_one_dashboard_per_data_source() -> None:
    assert {p.stem for p in DASHBOARDS} == {"pollen", "fuel", "trains-buses", "roads", "events"}


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
