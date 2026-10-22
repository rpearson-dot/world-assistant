"""The Home Assistant setup page's data: the files of this version, what changed when, the secrets they use."""

from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from app import ha_setup
from app.main import app

H = {"Authorization": "Bearer tok"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("API_TOKEN", "tok")
    return TestClient(app)


def test_the_manifest_matches_the_files() -> None:
    """Change a Home Assistant file and this fails until manifest.json says which release changed it."""
    listed = json.loads((ha_setup.HA_DIR / "manifest.json").read_text())["files"]
    on_disk = {p.relative_to(ha_setup.HA_DIR).as_posix() for p in ha_setup.HA_DIR.rglob("*.yaml")}
    assert set(listed) == on_disk, "add new files to homeassistant/manifest.json (and remove deleted ones)"
    for rel, meta in listed.items():
        digest = hashlib.sha256((ha_setup.HA_DIR / rel).read_bytes()).hexdigest()
        assert digest == meta["sha256"], (
            f"{rel} changed: in homeassistant/manifest.json set its changed_in to the coming release "
            f'and its sha256 to "{digest}"'
        )
        assert meta["changed_in"].count(".") == 2


def test_every_secret_a_package_uses_is_explained() -> None:
    used = {s for f in ha_setup.files() for s in f["secrets"]}
    assert used - {"wa_authorization"} <= set(ha_setup.SECRET_PATHS)
    events = next(f for f in ha_setup.files() if f["path"] == "packages/world_assistant_events.yaml")
    assert {"wa_events_url", "wa_authorization"} <= set(events["secrets"]) and events["kind"] == "package"
    assert all(f["title"] and f["changed_in"] for f in ha_setup.files())


def test_setup_lists_this_versions_files(client: TestClient) -> None:
    d = client.get("/v1/ha/setup", headers=H).json()
    kinds = {f["kind"] for f in d["files"]}
    assert kinds == {"package", "dashboard"} and d["version"]
    assert d["mqtt"]["state"] in {"off", "waiting", "ready", "failing"}


def test_only_the_shipped_files_can_be_fetched(client: TestClient) -> None:
    r = client.get("/v1/ha/files/dashboards/fuel.yaml", headers=H)
    assert r.status_code == 200 and "text/yaml" in r.headers["content-type"] and "views" in r.text
    for bad in ("/v1/ha/files/dashboards/..%2F..%2Fapp%2Fconfig.py", "/v1/ha/files/x/manifest.json",
                "/v1/ha/files/packages/nope.yaml"):  # fmt: skip
        assert client.get(bad, headers=H).status_code == 404, bad
    assert client.get("/v1/ha/files/dashboards/fuel.yaml").status_code == 401  # TestClient is not local


def test_the_page_loads(client: TestClient) -> None:
    r = client.get("/ha")
    assert r.status_code == 200 and "<!--NAV-->" not in r.text and 'href="/ha"' in r.text
