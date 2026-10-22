"""Copying the Home Assistant files into its config share over SMB."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

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
    assert first["ok"] and {f["result"] for f in first["files"]} == {"written"}
    assert (local / "packages" / "world_assistant_test.yaml").read_text() == stamp + "a: 1\n"
    again = copy_files(share, user, pw, files, None)
    assert {f["result"] for f in again["files"]} == {"unchanged"}
    assert copy_files(share, user, "wrong", files, None)["error"].startswith("the share refused")
