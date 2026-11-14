"""World Assistant as the charger's OCPP server: frames, answers, meter values, commands, and (with a database) a
charger connecting, booting, charging, being stopped and restarting, all recorded."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from starlette.websockets import WebSocketDisconnect

from app.modules.ocpp import ha, protocol

URL = os.environ.get("WA_TEST_DATABASE_URL")


def test_frames_are_read_and_written() -> None:
    f = protocol.parse('[2,"a1","Heartbeat",{}]')
    assert (f.kind, f.unique_id, f.action, f.payload) == (2, "a1", "Heartbeat", {})
    r = protocol.parse('[3,"x",{"status":"Accepted"}]')
    assert r.kind == 3 and r.payload == {"status": "Accepted"}
    e = protocol.parse('[4,"x","NotSupported","no",{}]')
    assert (e.error_code, e.error_text) == ("NotSupported", "no")
    uid, raw = protocol.call("Reset", {"type": "Soft"})
    assert json.loads(raw) == [2, uid, "Reset", {"type": "Soft"}]
    for bad in ("nope", "{}", "[9,\"x\"]", '[2,"x","Heartbeat"]'):
        with pytest.raises(protocol.FrameError):
            protocol.parse(bad)
    with pytest.raises(protocol.FrameError) as exc:
        protocol.parse('[2,"keep","Heartbeat",[]]')
    assert exc.value.unique_id == "keep"  # answered with an error, by its id


def test_a_restart_is_told_from_a_first_boot() -> None:
    boot = {"chargePointVendor": "SyncEV", "chargePointModel": "EVCP-7KW", "firmwareVersion": "1.6.2"}
    first = protocol.respond("BootNotification", boot, connected_s=3, booted_before=False, transaction_id=0)
    assert first.answer["status"] == "Accepted" and first.answer["interval"] == protocol.HEARTBEAT_S
    assert first.events[0][1].startswith("Charger started up (SyncEV EVCP-7KW, firmware 1.6.2)")
    again = protocol.respond("BootNotification", boot, connected_s=4000, booted_before=True, transaction_id=0)
    assert "restarted while still connected" in again.events[0][1]


def test_status_faults_and_sessions() -> None:
    e = protocol.respond("StatusNotification", {"connectorId": 1, "status": "Faulted", "errorCode": "GroundFailure",
                                                "vendorErrorCode": "E12"}, connected_s=60, booted_before=True,
                         transaction_id=0, last_status="Charging")  # fmt: skip
    assert e.charger["status"] == "Faulted" and e.charger["error_code"] == "GroundFailure"
    assert [k for k, _, _ in e.events] == ["fault", "status"] and "E12" in e.events[0][1]
    same = protocol.respond("StatusNotification", {"connectorId": 1, "status": "Charging", "errorCode": "NoError"},
                            connected_s=60, booted_before=True, transaction_id=0, last_status="Charging")  # fmt: skip
    assert same.events == []
    st = protocol.respond("StartTransaction", {"connectorId": 1, "idTag": "WorldAssistant", "meterStart": 1000,
                                               "timestamp": "2026-11-13T22:00:00Z"}, connected_s=60, booted_before=True, transaction_id=7)  # fmt: skip
    assert st.answer["transactionId"] == 7 and st.session_start["meter_start_wh"] == 1000
    sp = protocol.respond("StopTransaction", {"transactionId": 7, "meterStop": 5600, "reason": "Remote"},
                          connected_s=60, booted_before=True, transaction_id=0)  # fmt: skip
    assert sp.session_stop["reason"] == "Remote" and "stopped (Remote" in sp.events[0][1]
    assert protocol.respond("SomethingNew", {}, connected_s=1, booted_before=False, transaction_id=0).error[0] == "NotImplemented"


def test_meter_values_in_any_unit_and_phase() -> None:
    m = protocol.meter([
        {"timestamp": "2026-11-13T22:00:00Z", "sampledValue": [{"value": "12.5", "unit": "kWh"}]},
        {"timestamp": "2026-11-13T22:01:00Z", "sampledValue": [
            {"value": "12.6", "measurand": "Energy.Active.Import.Register", "unit": "kWh"},
            {"value": "4.6", "measurand": "Power.Active.Import", "unit": "kW"},
            {"value": "10", "measurand": "Current.Import", "unit": "A", "phase": "L1"},
            {"value": "10", "measurand": "Current.Import", "unit": "A", "phase": "L2"},
            {"value": "230", "measurand": "Voltage", "unit": "V", "phase": "L1-N"},
            {"value": "231", "measurand": "Voltage", "unit": "V", "phase": "L2-N"},
            {"value": "x", "measurand": "SoC"}]},
    ])  # fmt: skip
    assert m == {"energy_wh": 12600.0, "power_w": 4600.0, "current_a": 20.0, "voltage_v": 230.0, "at": "2026-11-13T22:01:00Z"}


def test_commands_are_checked_before_they_go() -> None:
    assert protocol.command_payload("RemoteStartTransaction", {}, transaction_id=None) == {"connectorId": 1, "idTag": "WorldAssistant"}
    assert protocol.command_payload("RemoteStopTransaction", {}, transaction_id=12) == {"transactionId": 12}
    with pytest.raises(ValueError, match="no charging session"):
        protocol.command_payload("RemoteStopTransaction", {}, transaction_id=None)
    with pytest.raises(ValueError):
        protocol.command_payload("Reset", {"type": "Explode"}, transaction_id=None)
    with pytest.raises(ValueError):
        protocol.command_payload("UpdateFirmware", {}, transaction_id=None)
    assert protocol.command_payload("TriggerMessage", {"message": "MeterValues", "connector": 1}, transaction_id=None) == \
        {"requestedMessage": "MeterValues", "connectorId": 1}  # fmt: skip


def test_home_assistant_sees_the_charger() -> None:
    msgs = ha.discovery("U1HKP")
    ids = [json.loads(p)["default_entity_id"] for _, p, _ in msgs]
    assert "switch.wa_ocpp_charging" in ids and "sensor.wa_ocpp_status" in ids and "binary_sensor.wa_ocpp_connected" in ids
    sw = next(json.loads(p) for topic, p, _ in msgs if "/switch/" in topic)
    assert sw["command_topic"].endswith("/ocpp/U1HKP/charging/set")
    st = ha.state({"status": "Charging", "meter": {"energy_wh": 13600, "power_w": 4600}, "error_code": "NoError"},
                  {"transaction_id": 3, "meter_start_wh": 12000, "last_meter_wh": 13600, "stopped_at": None})  # fmt: skip
    assert st == {"status": "Charging", "power_kw": 4.6, "energy_kwh": 13.6, "session_kwh": 1.6, "current_a": None,
                  "error": "NoError", "booted_at": None, "session": True, "transaction": 3, "mode": "auto"}  # fmt: skip
    idle = ha.state({"status": "Available", "meter": {"power_w": 4600}}, None)
    assert idle["power_kw"] == 0.0 and idle["session"] is False


# --- a charger's evening, end to end (database) ---------------------------------------------------------------------


class FakeRedis:
    def __init__(self) -> None:
        self.d: dict[str, Any] = {}

    async def get(self, k: str) -> Any:
        return self.d.get(k)

    async def set(self, k: str, v: Any, ex: int | None = None) -> None:
        self.d[k] = v

    async def delete(self, k: str) -> None:
        self.d.pop(k, None)

    async def publish(self, *_a: Any) -> None:
        return None


@pytest.fixture
def db() -> Iterator[str]:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mig = importlib.import_module("migrations.versions.0049_ocpp")
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        for t in ("ocpp_session", "ocpp_event", "ocpp_message", "ocpp_charger", "app_setting"):
            conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
        conn.execute(text("CREATE TABLE app_setting (key text primary key, value jsonb not null, updated_at timestamptz not null default now())"))
        with Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
    yield URL  # type: ignore[misc]
    with sync.begin() as conn:
        for t in ("ocpp_session", "ocpp_event", "ocpp_message", "ocpp_charger", "app_setting"):
            conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
    sync.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
def test_a_charger_connects_charges_is_stopped_and_restarts(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.main import app
    from app.modules.ocpp import server

    monkeypatch.setenv("API_TOKEN", "s3cret")
    monkeypatch.setattr(server, "private_peer", lambda ws: True)
    monkeypatch.setattr(server, "proxied", lambda ws: False)
    monkeypatch.setattr(ha, "started", lambda hub, cid: None)
    monkeypatch.setattr(ha, "changed", lambda hub, cid, with_discovery=False: None)

    async def on(_m: str, _e: Any) -> bool:
        return True

    monkeypatch.setattr("app.modules.gate.is_enabled", on)

    async def no_meter(self: Any, link: Any, every: int) -> None:
        return None

    monkeypatch.setattr(server.Hub, "_meter", no_meter)
    app.state.engine = create_async_engine(db, poolclass=NullPool)
    app.state.redis = FakeRedis()
    server.HUB.engine, server.HUB.redis = app.state.engine, app.state.redis
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def quiet(_app: Any) -> Any:  # no real database or Redis start-up: the test sets them
        yield

    monkeypatch.setattr(app.router, "lifespan_context", quiet)
    with TestClient(app) as c:  # one event loop for the socket and the requests
        _evening(c, app)
    asyncio.run(app.state.engine.dispose())


def _evening(c: Any, app: Any) -> None:
    h = {"Authorization": "Bearer s3cret"}

    def call(ws: Any, uid: str, action: str, payload: dict[str, Any]) -> Any:
        ws.send_text(json.dumps([2, uid, action, payload]))
        return json.loads(ws.receive_text())

    with c.websocket_connect("/ocpp/U1HKP", subprotocols=["ocpp1.6"]) as ws:
        assert ws.accepted_subprotocol == "ocpp1.6"
        assert call(ws, "1", "BootNotification", {"chargePointVendor": "SyncEV", "chargePointModel": "EVCP"})[2]["status"] == "Accepted"
        call(ws, "2", "StatusNotification", {"connectorId": 1, "status": "Preparing", "errorCode": "NoError"})
        start = call(ws, "3", "StartTransaction", {"connectorId": 1, "idTag": "WorldAssistant", "meterStart": 12000,
                                                    "timestamp": datetime.now(UTC).isoformat()})  # fmt: skip
        assert start[2]["transactionId"] == 1
        call(ws, "4", "StatusNotification", {"connectorId": 1, "status": "Charging", "errorCode": "NoError"})
        call(ws, "5", "MeterValues", {"connectorId": 1, "transactionId": 1, "meterValue": [
            {"timestamp": datetime.now(UTC).isoformat(), "sampledValue": [{"value": "13600", "unit": "Wh"},
                                                                           {"value": "4600", "measurand": "Power.Active.Import", "unit": "W"}]}]})  # fmt: skip

        # Stop from the API: the command goes down this socket; the "charger" answers it
        import threading

        got: dict[str, Any] = {}
        t = threading.Thread(target=lambda: got.update(r=c.post("/v1/ocpp/U1HKP/stop", headers=h).json()))
        t.start()
        cmd = json.loads(ws.receive_text())
        assert cmd[2] == "RemoteStopTransaction" and cmd[3] == {"transactionId": 1}
        ws.send_text(json.dumps([3, cmd[1], {"status": "Accepted"}]))
        t.join(10)
        assert got["r"]["ok"] is True and got["r"]["answer"] == {"status": "Accepted"}
        call(ws, "6", "StopTransaction", {"transactionId": 1, "meterStop": 13700, "reason": "Remote",
                                          "timestamp": datetime.now(UTC).isoformat()})  # fmt: skip
        # in Auto, with the plan wanting a charge: a fault, then back to ready, and the session is started again
        app.state.redis.d["wa:ocpp:wanted:U1HKP"] = "1"
        call(ws, "6a", "StatusNotification", {"connectorId": 1, "status": "Faulted", "errorCode": "OtherError", "vendorErrorCode": "E12"})
        call(ws, "6b", "StatusNotification", {"connectorId": 1, "status": "Preparing", "errorCode": "NoError"})
        again = json.loads(ws.receive_text())
        assert again[2] == "RemoteStartTransaction" and again[3]["connectorId"] == 1
        ws.send_text(json.dumps([3, again[1], {"status": "Accepted"}]))
        # it restarts without dropping the link
        call(ws, "7", "BootNotification", {"chargePointVendor": "SyncEV", "chargePointModel": "EVCP"})
        bad = ws.send_text("[2,\"8\",\"Heartbeat\",[]]") or json.loads(ws.receive_text())
        assert bad[0] == 4 and bad[1] == "8"
        ws.close()  # and let the server record the drop before the test client cancels it
        __import__("time").sleep(0.5)

    import time

    for _ in range(50):  # the drop is recorded as the socket closes, just after the client has gone
        ch = c.get("/v1/ocpp", headers=h).json()["chargers"][0]
        if ch["week"]["drops"]:
            break
        time.sleep(0.1)
    assert ch["id"] == "U1HKP" and ch["boot"]["chargePointVendor"] == "SyncEV" and ch["week"]["boots"] == 2
    assert ch["week"]["drops"] == 1 and ch["meter"]["energy_wh"] == 13600, c.get("/v1/ocpp/U1HKP/events", headers=h).json()
    s = c.get("/v1/ocpp/U1HKP/sessions", headers=h).json()["sessions"][0]
    assert (s["kwh"], s["reason"]) == (1.7, "Remote")
    ev = [e["text"] for e in c.get("/v1/ocpp/U1HKP/events", headers=h).json()["events"]]
    assert any("restarted while still connected" in e for e in ev) and any(e.startswith("Disconnected after") for e in ev)
    assert any(e == "RemoteStopTransaction: Accepted" for e in ev)
    assert any(e.startswith("Charging again after the charger stopped") for e in ev)
    assert any(e.startswith("Fault: OtherError (E12)") for e in ev)
    msgs = c.get("/v1/ocpp/U1HKP/messages", headers=h).json()["messages"]
    # 9 calls and their answers, the stop and the start sent again with their answers, the error
    assert {m["direction"] for m in msgs} == {"in", "out"} and len(msgs) == 23
    # not a listed charger: refused once any are listed
    assert c.put("/v1/ocpp/settings", json={"chargers": ["U1HKP"]}, headers=h).status_code == 200
    with pytest.raises(WebSocketDisconnect), c.websocket_connect("/ocpp/OTHER") as ws2:
        ws2.receive_text()
    assert c.post("/v1/ocpp/U1HKP/start", headers=h).status_code == 409  # not connected now


def test_the_master_switch_is_offered_to_home_assistant() -> None:
    from app.modules.ocpp import ha

    topics = {t: json.loads(p) for t, p, _ in ha.discovery("U1HKP")}
    sel = next(v for t, v in topics.items() if "/select/" in t)
    assert sel["options"] == ["Off", "On", "Auto"] and sel["command_topic"].endswith("/ocpp/U1HKP/mode/set")
    assert ha.state({"status": "Charging"}, None, "on")["mode"] == "on"


def test_the_address_given_to_a_charger_is_on_the_home_network() -> None:
    from app.modules.ocpp.api import address

    assert address("10.0.2.20:8010") == "ws://10.0.2.20:8010/ocpp/"
    assert address("192.168.1.5") == "ws://192.168.1.5:8010/ocpp/"
    for other in ("world-assistant.tailad4b24.ts.net", "100.101.2.3:8010", "localhost:8010", None):
        assert address(other) == "ws://<this server's home network address>:8010/ocpp/"
