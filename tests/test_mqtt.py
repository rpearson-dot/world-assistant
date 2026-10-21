from __future__ import annotations

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app import mqtt
from app.integrations.bus.config import TrackerConfig
from app.integrations.bus.poller import build_snapshot

TZ = ZoneInfo("Europe/London")
CFG = TrackerConfig.model_validate({
    "line": "213",
    "stops": [{"key": "a", "sid": "AAAA0001", "label": "Stop A"}, {"key": "b", "sid": "AAAA0002", "label": "Stop B"}],
    "target": {"board": "a", "alight": "b"},
})  # fmt: skip


def test_discovery_messages_are_retained_json_with_unique_ids() -> None:
    msgs = mqtt.discovery_messages("wa", "homeassistant", "main", [("a", "Stop A")])
    topics = [m[0] for m in msgs]
    assert all(
        t.startswith("homeassistant/sensor/wa_bus_main_") and t.endswith("/config") for t in topics
    )
    assert len(set(topics)) == len(topics)
    assert all(m[2] for m in msgs)
    payloads = [json.loads(m[1]) for m in msgs]
    assert len({p["unique_id"] for p in payloads}) == len(payloads)
    nxt = next(p for p in payloads if p["unique_id"].endswith("next_a"))
    assert nxt["state_topic"] == "wa/bus/main/next_a" and nxt["unit_of_measurement"] == "min"


def test_snapshot_and_state_messages() -> None:
    hit = {"vehicle": "V1", "arrive_at": datetime(2026, 10, 20, 6, 55, tzinfo=UTC), "slot": "07:30",
           "delay_min": 2.0, "via_return": False}  # fmt: skip
    preds = [{"naptanId": "AAAA0001", "timeToStation": 300, "vehicleId": "V2"},
             {"naptanId": "AAAA0001", "timeToStation": 900, "vehicleId": "V3"}]  # fmt: skip
    snap = build_snapshot(CFG, [hit], ["07:30", "08:00"], preds, {"trips": {"V2": {}}}, TZ)
    assert snap["last_bus"]["state"] == "07:55"
    assert snap["hits_today"]["state"] == 1 and snap["pending_slots"]["attributes"]["slots"] == [
        "08:00"
    ]
    assert snap["active_buses"]["state"] == 1
    assert snap["next_a"]["state"] == 5 and snap["next_a"]["attributes"]["next_minutes"] == [5, 15]
    assert snap["next_b"]["state"] is None
    msgs = {t: p for t, p, _ in mqtt.state_messages("wa", "main", snap)}
    assert msgs["wa/bus/main/last_bus"] == "07:55" and msgs["wa/bus/main/next_b"] == ""
    assert json.loads(msgs["wa/bus/main/last_bus/attributes"])["vehicle"] == "V1"


def test_snapshot_with_no_buses_yet() -> None:
    snap = build_snapshot(CFG, [], ["07:30"], [], {"trips": {}}, TZ)
    assert snap["last_bus"]["state"] == "none" and "No bus" in snap["summary"]["state"]
