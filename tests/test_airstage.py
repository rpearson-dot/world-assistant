"""Fujitsu Airstage units on the home network: reading their values, checking a change, the local API's requests,
and Home Assistant's commands."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.modules.airstage import ha, unit

RAW = {"iu_onoff": "1", "iu_op_mode": "4", "iu_fan_spd": "8", "iu_set_tmp": "215", "iu_indoor_tmp": "7050",
       "iu_outdoor_tmp": "5630", "iu_pow_cons": "1234", "iu_err_code": "0", "iu_fltr_sign_reset": "0",
       "iu_af_swg_vrt": "0", "iu_af_swg_hrz": "65535", "iu_economy": "1", "iu_model": "ASYG09KMCC"}  # fmt: skip


def test_values_are_read_as_people_say_them() -> None:
    s = unit.state(RAW)
    assert (s["power"], s["mode"], s["fan"], s["target_c"], s["indoor_c"], s["outdoor_c"]) == (True, "heat", "medium", 21.5, 20.5, 6.3)
    assert s["swing_horizontal"] is None and s["economy"] is True and s["error_code"] is None and s["energy"] == 1234.0
    assert unit.state({**RAW, "iu_err_code": "E5"})["error_code"] == "E5"


def test_a_change_is_checked_before_it_is_sent() -> None:
    cur = unit.state(RAW)
    assert unit.changes({"power": True, "mode": "cool", "target_c": 22.3}, cur) == [("iu_onoff", "1"), ("iu_op_mode", "1"), ("iu_set_tmp", "225")]
    assert unit.changes({"fan": "quiet", "swing_vertical": True}, cur) == [("iu_fan_spd", "2"), ("iu_af_swg_vrt", "1")]
    with pytest.raises(ValueError, match="16 to 30"):
        unit.changes({"target_c": 14}, cur)  # heat: 16 to 30
    with pytest.raises(ValueError, match="fan mode"):
        unit.changes({"mode": "fan_only", "target_c": 20}, cur)
    with pytest.raises(ValueError):
        unit.changes({"mode": "turbo"}, cur)


def test_the_units_own_api_is_asked_politely() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    def handle(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append((req.url.path, body))
        if req.url.path == "/GetParam":
            assert len(body["list"]) <= 20 and body["device_id"] == "A0B1C2D3E4F5"
            return httpx.Response(200, json={"result": "OK", "value": {k: RAW.get(k, "65535") for k in body["list"]}})
        return httpx.Response(200, json={"result": "OK"})

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
            await unit.write(c, "10.0.2.150", "A0B1C2D3E4F5", [("iu_onoff", "0")])
            return await unit.read(c, "10.0.2.150", "A0B1C2D3E4F5")

    raw = asyncio.run(go())
    assert seen[0] == ("/SetParam", {"device_id": "A0B1C2D3E4F5", "device_sub_id": 0, "req_id": "", "modified_by": "",
                                     "set_level": "02", "value": {"iu_onoff": "0"}})  # fmt: skip
    assert [p for p, _ in seen[1:]] == ["/GetParam", "/GetParam"] and raw["iu_set_tmp"] == "215"


def test_home_assistant_sees_a_climate_entity_and_its_commands_are_changes() -> None:
    topic, cfg, _ = ha.discovery({"id": "bedroom", "name": "Bedroom", "ip": "10.0.2.150", "device_id": "x"})
    c = json.loads(cfg)
    assert topic.endswith("/climate/wa_aircon_bedroom/config") and "heat" in c["modes"] and c["temp_step"] == 0.5
    assert ha.payload({**unit.state(RAW), "power": False})["hvac"] == "off"
    assert ha.payload(unit.state(RAW))["hvac"] == "heat"
    assert ha.command("mode", "off") == {"power": False}
    assert ha.command("mode", "cool") == {"power": True, "mode": "cool"}
    assert ha.command("target_c", "21.5") == {"target_c": 21.5}
    assert ha.command("swing", "on") == {"swing_vertical": True}


def test_odd_answers_never_break_the_reading() -> None:
    s = unit.state({**RAW, "iu_onoff": ["1"], "iu_set_tmp": "215.0", "iu_pow_cons": "12.5", "iu_indoor_tmp": {"x": 1}})
    assert s["power"] is True and s["target_c"] == 21.5 and s["energy"] == 12.5 and s["indoor_c"] is None
    assert unit.state("not a reading")["power"] is None  # type: ignore[arg-type]


URL = __import__("os").environ.get("WA_TEST_DATABASE_URL")


@pytest.mark.skipif(not URL, reason="needs WA_TEST_DATABASE_URL")
def test_the_page_lists_units_that_have_answered() -> None:
    """The 14 November fault: once a unit had answered, listing the units failed (500)."""
    import importlib

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.modules.airstage import api

    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS airstage_reading"))
        mig = importlib.import_module("migrations.versions.0051_airstage")
        mig.op = Operations(MigrationContext.configure(conn))  # type: ignore[attr-defined]
        mig.upgrade()

    async def go() -> dict[str, Any]:
        engine = create_async_engine(URL)  # type: ignore[arg-type]
        try:
            await api._keep(engine, "lounge", RAW, None)
            await api._keep(engine, "lounge", None, "no answer")
            await api._keep(engine, "hall", None, "no answer")
            async with engine.connect() as conn:
                return await api.latest(conn)
        finally:
            await engine.dispose()

    try:
        got = asyncio.run(go())
    finally:
        with sync.begin() as conn:
            conn.execute(sa.text("DROP TABLE IF EXISTS airstage_reading"))
        sync.dispose()
    assert got["lounge"]["ok"] is False and got["lounge"]["state"]["target_c"] == 21.5 and got["lounge"]["good_at"] is not None
    assert got["hall"]["state"] is None and got["hall"]["error"] == "no answer"
