"""Copying the Home Assistant files into its config share over SMB."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.ha_setup import SECRET_PATHS
from app.ha_smb import Share, copy_files, differing, merge_secrets, parse_share, secret_lines
from app.main import app

URL = os.environ.get("WA_TEST_DATABASE_URL")
# A real share to write to, if there is one: "//host:port/share user password /local/path/of/the/share"
SMB = os.environ.get("WA_TEST_SMB", "").split()
H = {"Authorization": "Bearer tok"}


def test_share_addresses() -> None:
    assert parse_share(r"\\homeassistant.local\config") == Share("homeassistant.local", "config", "", 445)
    assert parse_share("//192.168.1.20/config/") == Share("192.168.1.20", "config", "", 445)
    assert parse_share("smb://192.168.1.20:4445/config/sub/dir") == Share("192.168.1.20", "config", "sub/dir", 4445)
    assert parse_share(r"\\ha\config").unc("packages/x.yaml") == r"\\ha\config\packages\x.yaml"
    for bad in ("homeassistant/config", r"\\ha", "//ha/config/../etc", "//ha:99999/config", "http://ha/config"):
        with pytest.raises(ValueError):
            parse_share(bad)


def test_secrets_are_only_ever_added() -> None:
    wanted = secret_lines({"wa_events_url", "wa_authorization", "wa_nonsense"}, "http://192.168.1.10:8010", "T", SECRET_PATHS)
    assert wanted == {"wa_authorization": 'wa_authorization: "Bearer T"',
                      "wa_events_url": 'wa_events_url: "http://192.168.1.10:8010/v1/events/near"'}  # fmt: skip
    current = 'existing: "keep"\nwa_events_url: "http://old:8010/v1/events/near"'
    new, added = merge_secrets(current, wanted)
    assert added == ["wa_authorization"] and new.startswith(current + "\n")
    assert 'wa_events_url: "http://old:8010' in new and new.count("wa_events_url") == 1  # never changed
    assert differing(current, wanted) == ["wa_events_url"]  # but reported
    assert merge_secrets(new, wanted) == (new, [])  # a second copy adds nothing


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    if not URL:
        pytest.skip("WA_TEST_DATABASE_URL not set")
    monkeypatch.setenv("API_TOKEN", "tok")
    monkeypatch.setenv("WA_KEYS_DIR", str(tmp_path))
    get_settings.cache_clear()
    sync = sa.create_engine(URL)
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS app_setting"))
        conn.execute(text("CREATE TABLE app_setting (key text primary key, value jsonb not null,"
                          " updated_at timestamptz not null default now())"))  # fmt: skip
    app.state.engine = create_async_engine(URL, poolclass=NullPool)
    yield
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS app_setting"))
    sync.dispose()
    get_settings.cache_clear()


def test_the_share_is_kept_but_never_its_password(db: None, tmp_path: Path) -> None:
    c = TestClient(app)
    assert c.post("/v1/ha/smb/copy", headers=H, json={}).status_code == 409  # nothing set up yet
    assert c.put("/v1/ha/smb", headers=H, json={"address": "homeassistant/config"}).status_code == 422
    r = c.put("/v1/ha/smb", headers=H, json={"address": r"\\ha.local\config", "user": "me", "password": "pw"}).json()
    assert r["address"] == r"\\ha.local\config" and r["password_set"] is True and "pw" not in str(r)
    assert (tmp_path / "ha_smb_password.txt").read_text() == "pw"
    bad = c.post("/v1/ha/smb/copy", headers=H, json={"packages": ["../../app/config.py"]})
    assert bad.status_code == 422  # only this version's packages
    gone = c.delete("/v1/ha/smb", headers=H).json()
    assert gone["address"] == "" and not (tmp_path / "ha_smb_password.txt").exists()


@pytest.mark.skipif(len(SMB) != 4, reason="WA_TEST_SMB not set (a real share to write to)")
def test_copying_to_a_real_share() -> None:
    share, user, pw, local = parse_share(SMB[0]), SMB[1], SMB[2], Path(SMB[3])
    import time

    stamp = f"# {time.time_ns()}\n"  # new content every run, so the first copy always writes
    files = {"packages/world_assistant_test.yaml": stamp + "a: 1\n", "world_assistant/dashboards/test.yaml": stamp}
    first = copy_files(share, user, pw, files, None)
    assert first["ok"] and {f["result"] for f in first["files"]} == {"written"}, first
    assert (local / "packages" / "world_assistant_test.yaml").read_text() == stamp + "a: 1\n"
    again = copy_files(share, user, pw, files, None)
    assert {f["result"] for f in again["files"]} == {"unchanged"}, again
    assert copy_files(share, user, "wrong", files, None)["error"].startswith("the share refused")


def test_the_inverter_name_is_saved_and_used_in_every_download_and_copy(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import ha_setup, ha_smb

    c = TestClient(app)
    d = c.get("/v1/ha/setup", headers={"Authorization": "Bearer tok"}).json()
    assert d["inverter"] == "office_inverter"  # as shipped, until one is saved
    plan = next(f for f in d["files"] if f["path"] == "packages/wa_energy_plan.yaml")
    assert plan["inverter"] and not next(f for f in d["files"] if f["path"] == "dashboards/fuel.yaml")["inverter"]
    h = {"Authorization": "Bearer tok"}
    assert c.put("/v1/ha/inverter", headers=h, json={"name": "Garage Inverter"}).status_code == 422
    assert c.put("/v1/ha/inverter", headers=h, json={"name": "sensor.garage_deye_battery_soc"}).json() == {"inverter": "garage_deye"}
    assert c.get("/v1/ha/setup", headers=h).json()["inverter"] == "garage_deye"
    for path in ("packages/wa_energy_plan.yaml", "dashboards/home-charging.yaml"):
        body = c.get(f"/v1/ha/files/{path}", headers=h).text
        assert "office_inverter" not in body and "sensor.garage_deye_battery_soc" in body, path

    sent: dict[str, str] = {}

    def fake_copy(share: object, user: str, pw: str, files: dict[str, str], secrets: object, *rest: object) -> dict[str, object]:
        sent.update(files)
        return {"ok": False, "error": "test"}

    async def conf(request: object) -> tuple[object, str, str]:
        return ha_smb.parse_share(r"\\ha\config"), "u", "p"

    monkeypatch.setattr(ha_smb, "copy_files", fake_copy)
    monkeypatch.setattr(ha_smb, "_conf", conf)
    c.post("/v1/ha/smb/copy", headers=h, json={"packages": ["packages/wa_energy_plan.yaml"], "dashboards": True})
    assert "switch.garage_deye_time_slot_" in sent["packages/wa_energy_plan.yaml"]
    assert "office_inverter" not in sent["packages/wa_energy_plan.yaml"]
    assert "garage_deye" in sent["world_assistant/dashboards/home-charging.yaml"]
    assert ha_setup.inverter_name("  OFFICE_INVERTER ") == "office_inverter"


def test_find_my_setup_reads_checks_and_names(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import ha_discover, ha_smb
    from tests.test_ha_discover import RAW

    async def conf(request: object) -> tuple[object, str, str]:
        return ha_smb.parse_share(r"\\ha\config"), "u", "p"

    asked: list[list[str]] = []

    def read(share: object, user: str, pw: str, paths: list[str]) -> dict[str, str | None]:
        asked.append(paths)
        return dict(RAW)

    monkeypatch.setattr(ha_smb, "_conf", conf)
    monkeypatch.setattr(ha_discover, "read_share", read)
    c = TestClient(app)
    h = {"Authorization": "Bearer tok"}
    assert c.get("/v1/ha/inventory", headers=h).json() == {}
    inv = c.post("/v1/ha/discover", headers=h).json()
    assert "packages/wa_energy_plan.yaml" in asked[0] and "world_assistant/dashboards/home-charging.yaml" in asked[0]
    assert inv["names"]["ocpp_charger"]["found"] == "wallbox" and inv["version"] == "2026.10.2"
    assert c.get("/v1/ha/inventory", headers=h).json()["read_at"] == inv["read_at"]  # kept
    # save what was found: every file is written with it
    names = {k: v["found"] for k, v in inv["names"].items() if v["found"]}
    saved = c.put("/v1/ha/names", headers=h, json={"names": names}).json()["names"]
    assert saved["inverter"] == "garage_deye" and saved["solar_today"] == "sensor.energy_production_today_2"
    ev = c.get("/v1/ha/files/packages/wa_ev_plan.yaml", headers=h).text
    assert "switch.wallbox_charge_control" in ev and "hv66bzd" not in ev and "sensor.charger_" not in ev
    plan = c.get("/v1/ha/files/packages/wa_energy_plan.yaml", headers=h).text
    assert "office_inverter" not in plan and "sensor.energy_production_tomorrow_2" in plan
    assert c.get("/v1/ha/setup", headers=h).json()["names"]["ovms_vehicle"] == "ab12cde"
    assert c.put("/v1/ha/names", headers=h, json={"names": {"solar_today": "nonsense"}}).status_code == 422
    back = c.put("/v1/ha/names", headers=h, json={"names": {"inverter": ""}}).json()["names"]
    assert back["inverter"] == "office_inverter"  # empty: the default again


def test_zones_become_places_and_the_token_is_kept_safe(db: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app import ha_discover, ha_smb
    from tests.test_ha_discover import RAW

    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS place"))
        conn.execute(text("CREATE TABLE place (id text primary key, name text, kind text, postcode text, lat float8, "
                          "lon float8, updated_at timestamptz default now())"))  # fmt: skip
        conn.execute(text("INSERT INTO place (id, name, kind, lat, lon) VALUES ('home', 'Home', 'home', 51.38, -0.24)"))
    try:
        async def conf(request: object) -> tuple[object, str, str]:
            return ha_smb.parse_share(r"\\ha\config"), "u", "p"

        monkeypatch.setattr(ha_smb, "_conf", conf)
        monkeypatch.setattr(ha_discover, "read_share", lambda *a: dict(RAW))
        c, h = TestClient(app), {"Authorization": "Bearer tok"}
        inv = c.post("/v1/ha/discover", headers=h).json()
        assert [z["name"] for z in inv["zones"] if z["place"] is None] == ["Work"] and inv["places_not_zones"] == []
        made = c.post("/v1/ha/zones/import", headers=h, json={"names": ["Work", "Nowhere"]}).json()
        assert made["made"] == ["work"] and "Nowhere" in made["skipped"][0]
        assert "name: \"Work\"" not in c.get("/v1/ha/zones/package", headers=h).text  # nothing new to send back
        # the token: kept in the keys folder, never shown, tested on save
        async def live(url: str, token: str, transport: object = None) -> dict[str, Any]:
            return {"states": [{"entity_id": "sun.sun", "state": "x"}]} if token == "abc" else {"error": "refused"}

        monkeypatch.setattr(ha_discover, "live_states", live)
        d = c.put("/v1/ha/api", headers=h, json={"url": "http://10.0.2.107:8123/", "token": "abc"}).json()
        assert d == {"url": "http://10.0.2.107:8123", "token_set": True, "ok": True, "error": None, "entities": 1}
        assert (tmp_path / "ha_token.txt").read_text() == "abc" and oct((tmp_path / "ha_token.txt").stat().st_mode)[-3:] == "600"
        assert "abc" not in json.dumps(c.get("/v1/ha/api", headers=h).json())
        assert c.put("/v1/ha/api", headers=h, json={"url": "not a url"}).status_code == 422
        assert c.put("/v1/ha/api", headers=h, json={"url": "10.0.2.107:8123"}).json()["url"] == "http://10.0.2.107:8123"
        live_inv = c.post("/v1/ha/discover", headers=h).json()
        assert live_inv["live"] is True
        assert c.delete("/v1/ha/api", headers=h).json() == {"url": "", "token_set": False}
    finally:
        with sync.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS place"))
        sync.dispose()


def test_dashboards_go_into_configuration_yaml_once_and_never_into_yours() -> None:
    from app.ha_smb import merge_lovelace

    block = "lovelace:\n  dashboards:\n    wa-fuel:\n      mode: yaml\n      filename: world_assistant/dashboards/fuel.yaml\n"
    base = "default_config:\nhomeassistant:\n  packages: !include_dir_named packages\n"
    new, what = merge_lovelace(base, block, ["wa-fuel"])
    assert what == "added" and new is not None and new.startswith(base) and new.endswith(block)
    assert merge_lovelace(new, block, ["wa-fuel"]) == (None, "already set up")  # copying again changes nothing
    bigger = block + "    wa-home-charging:\n      mode: yaml\n"
    again, what = merge_lovelace(new + "\nautomation: !include automations.yaml\n", bigger, ["wa-fuel", "wa-home-charging"])
    assert what == "updated" and again is not None and again.count("lovelace:") == 1 and "wa-home-charging" in again
    assert again.rstrip().endswith("automation: !include automations.yaml")  # what follows our block is kept
    mine = base + "lovelace:\n  mode: storage\n"
    assert merge_lovelace(mine, block, ["wa-fuel"]) == (None, "manual")  # a lovelace: section of yours: never edited
    assert merge_lovelace(base + "lovelace:\n  dashboards:\n    wa-fuel:\n      mode: yaml\n", block, ["wa-fuel"]) == (None, "already set up")
