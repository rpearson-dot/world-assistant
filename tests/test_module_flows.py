"""Each module's role, inputs, outputs and controls (app/modules/flows.py) checked against its code: a module that
sends notifications, publishes to Home Assistant, reads Home Assistant or commands a device must say so."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.modules import registry
from app.modules.flows import FLOWS, flow

ROOT = Path(__file__).resolve().parents[1] / "app" / "modules"
MODULES = [m for m in registry.modules() if not m.core and m.source == "builtin"]
# The core's own Home Assistant publisher (app/ha_publish.py) makes these modules' devices
CORE_PUBLISHED = {"fuel", "pollen", "events", "tfl", "bus", "nh", "tflroads", "ukpn", "gridserve", "conditions", "wishlist",
                  "outdoors", "food", "vehiclechecks", "environment", "floods"}  # fmt: skip


def code(m: object) -> str:
    folder = ROOT / m.id  # type: ignore[attr-defined]
    return "\n".join(p.read_text() for p in folder.rglob("*.py")) if folder.exists() else ""


@pytest.mark.parametrize("m", MODULES, ids=lambda m: m.id)
def test_a_module_says_what_it_does_with_data(m: object) -> None:
    src, f = code(m), flow(m)
    assert m.role in ("source", "processor", "output"), f"{m.id}: role is source, processor or output"  # type: ignore[attr-defined]
    gives, reads = " ".join(m.gives).lower(), " ".join(m.reads).lower()  # type: ignore[attr-defined]
    if re.search(r"notify\.send\(|send_many\(", src):
        assert "notifications" in gives, f"{m.id} sends notifications: say so in gives"  # type: ignore[attr-defined]
    if "mqtt_discovery_prefix" in src or m.id in CORE_PUBLISHED:  # type: ignore[attr-defined]
        assert "home assistant" in gives, f"{m.id} publishes to Home Assistant: say what in gives"  # type: ignore[attr-defined]
    if re.search(r"ha_stats|live_states|ha_api\(|planner import .*hass", src):
        assert "home assistant" in reads, f"{m.id} reads Home Assistant: say what in reads"  # type: ignore[attr-defined]
    if re.search(r"RemoteStartTransaction|RemoteStopTransaction|SetParam|ChangeAvailability", src):
        assert m.controls, f"{m.id} commands a device: say what in controls"  # type: ignore[attr-defined]
    if m.role == "source":  # type: ignore[attr-defined]
        assert m.reads, f"{m.id} is a source: say what it reads"  # type: ignore[attr-defined]
    assert f["outputs"] or f["controls"], f"{m.id}: nothing comes out of it"  # type: ignore[attr-defined]


def test_every_flow_is_for_a_module_that_exists() -> None:
    ids = {m.id for m in registry.modules()}
    assert set(FLOWS) <= ids, set(FLOWS) - ids
