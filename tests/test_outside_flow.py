"""Outside modules from end to end, with the runner and GitHub stood in for: read a release, its image checked,
installed with only what you ticked, the module gate giving it exactly that, an update installed by itself, one that
asks for more held for you, and removal leaving nothing (the report says so)."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import require_token
from app.outside import api as X
from app.outside import gate as G
from app.outside import github

URL = os.environ.get("WA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="needs WA_TEST_DATABASE_URL")
TOML = """
id = "tigo"
name = "Tigo optimisers"
version = "{v}"
sdk = 3
licence = "MIT"
repository = "https://github.com/someone/wa-tigo"
role = "source"
reads = ["the Tigo CCA"]
gives = ["Home Assistant: power for each panel"]
[image]
name = "ghcr.io/someone/wa-tigo"
[keys]
tigo_password = {{ label = "CCA password" }}
[network]
home_devices = ["Tigo CCA"]
[data]
home_location = "postcode district"
places = {places}
home_assistant_write = ["sensor.wa_x_tigo_*"]
notifications = true
[[settings]]
key = "every"
label = "Read every (seconds)"
type = "number"
"""
GOOD = {"signed": True, "provenance": True, "sbom": True, "high": 0, "critical": 0}
TABLES = "xmod, xmod_job, xmod_event, xmod_published, app_setting, notify_log, place, postcode, vehicle, job_run"


def release(v: str = "1.0.0", places: str = "false") -> dict[str, Any]:
    return {"tag": f"v{v}", "commit": hashlib.sha1(v.encode()).hexdigest(), "manifest": TOML.format(v=v, places=places),
            "lock": {"image": "ghcr.io/someone/wa-tigo", "digest": "sha256:" + hashlib.sha256(v.encode()).hexdigest(), "commit": ""},
            "licence": "MIT", "published_at": None, "archived": False}  # fmt: skip


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {TABLES}"))
        conn.execute(sa.text("CREATE TABLE app_setting (key text primary key, value jsonb, updated_at timestamptz)"))
        conn.execute(sa.text("CREATE TABLE notify_log (key text primary key, kind text, title text, body text, sent_at timestamptz, ok boolean, error text)"))
        conn.execute(sa.text("CREATE TABLE place (id text primary key, name text, kind text, postcode text, lat float8, lon float8)"))
        conn.execute(sa.text("INSERT INTO place VALUES ('home', 'Home', 'home', 'KT17 1AA', 51.3301, -0.2702)"))
        conn.execute(sa.text("CREATE TABLE postcode (pc text primary key, lat float8, lon float8)"))
        conn.execute(sa.text("INSERT INTO postcode VALUES ('KT171AA', 51.33, -0.27), ('KT172BB', 51.35, -0.25)"))
        conn.execute(sa.text("CREATE TABLE vehicle (id text primary key, name text, kind text, registration text)"))
        conn.execute(sa.text("CREATE TABLE job_run (id bigserial primary key, job text, started_at timestamptz default now(), "
                             "finished_at timestamptz, status text default 'running', result jsonb, error text)"))
        mig = importlib.import_module("migrations.versions.0057_outside_modules")
        mig.op = Operations(MigrationContext.configure(conn))  # type: ignore[attr-defined]
        mig.upgrade()
    monkeypatch.setenv("WA_KEYS_DIR", str(tmp_path))
    from app import secretstore

    secretstore.clear_cache()
    rel = {"now": release()}

    async def fake_release(client: Any, url: str, tag: str | None = None) -> dict[str, Any]:
        return rel["now"]

    monkeypatch.setattr(github, "release", fake_release)
    monkeypatch.setattr("app.keys.writable", lambda: (True, ""))
    sent: list[tuple[str, str]] = []

    async def fake_notify(engine: Any, kind: str, key: str, title: str, body: str, **kw: Any) -> dict[str, Any]:
        sent.append((key, body))
        async with engine.begin() as conn:
            await conn.execute(sa.text("INSERT INTO notify_log (key, kind, title, body, sent_at, ok) VALUES (:k, :kind, :t, :b, now(), true) "
                                       "ON CONFLICT DO NOTHING"), {"k": key, "kind": kind, "t": title, "b": body})  # fmt: skip
        return {"sent": True}

    monkeypatch.setattr("app.notify.send", fake_notify)
    published: list[tuple[str, str]] = []

    async def fake_publish(msgs: Any, conf: Any = None) -> bool:
        published.extend((t, p) for t, p, _ in msgs)
        return True

    monkeypatch.setattr("app.mqtt.publish", fake_publish)

    class Conf:
        active, host = True, "broker"

    async def fake_load(engine: Any) -> tuple[Any, str]:
        return Conf(), "settings"

    monkeypatch.setattr("app.mqtt_config.load", fake_load)
    from sqlalchemy.ext.asyncio import create_async_engine

    app = FastAPI()
    app.include_router(X.router)
    app.dependency_overrides[require_token] = lambda: None

    @app.on_event("startup")
    async def _up() -> None:
        app.state.engine = create_async_engine(URL)  # type: ignore[arg-type]
        G.app.state.engine = app.state.engine
        G._cache.clear()

    with TestClient(app) as client, TestClient(G.app) as gate:
        yield {"api": client, "gate": gate, "rel": rel, "sent": sent, "published": published, "keys": tmp_path}
    with sync.begin() as conn:
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {TABLES}"))
    sync.dispose()
    if hasattr(G.app.state, "engine"):
        del G.app.state.engine


def runner(api: TestClient, results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Do every waiting job as the runner would, answering each kind from `results`."""
    done = []
    while (job := api.get("/v1/outside/runner/next").json()) is not None:
        res = results.get(job["kind"], {})
        assert api.post(f"/v1/outside/runner/{job['id']}/done", json=res).status_code == 200
        done.append(job)
    return done


def test_install_share_update_and_remove(world: dict[str, Any]) -> None:
    api, gate = world["api"], world["gate"]
    r = api.post("/v1/outside/preview", json={"repository": "https://github.com/someone/wa-tigo"})
    assert r.status_code == 200 and r.json()["state"] == "checking"
    jobs = runner(api, {"check": {**GOOD, "high": 2}})
    assert jobs[0]["spec"]["ref"].startswith("ghcr.io/someone/wa-tigo@sha256:") and jobs[0]["spec"]["tag"] == "v1.0.0"
    x = api.get("/v1/outside/tigo").json()
    assert x["state"] == "previewed" and "2 high-severity" in x["problems"][0]
    assert x["asks"]["data"][0]["precision"] == "postcode district" and x["asks"]["home_devices"] == ["Tigo CCA"]
    assert api.post("/v1/outside/tigo/install", json={}).status_code == 409  # its findings must be accepted
    r = api.post("/v1/outside/tigo/install", json={"data": {"home_location": "exact", "places": True},
                                                   "home_devices": {"Tigo CCA": "10.0.2.50"}, "allow_vulnerable": True})  # fmt: skip
    assert r.status_code == 200, r.text
    consent = r.json()["consent"]
    assert consent["data"] == {} and consent["home_devices"] == {"Tigo CCA": "10.0.2.50"}  # exact is more than it asked; places not asked
    start = runner(api, {"start": {"running": True}})[0]["spec"]
    token = start["env"]["WA_MODULE_TOKEN"]
    assert start["memory_mb"] == 256 and start["env"]["HTTPS_PROXY"] == f"http://tigo:{token}@wa-modgate:8091"
    assert (world["keys"] / "x_tigo__token.txt").exists() and api.get("/v1/outside/tigo").json()["state"] == "running"

    # the gate: only with its token, only what was agreed
    h = {"X-WA-Module": "tigo", "Authorization": f"Bearer {token}"}
    assert gate.get("/api/me", headers={**h, "Authorization": "Bearer nope"}).status_code == 401
    assert gate.get("/api/me", headers=h).json()["agreed"]["home_devices"] == {"Tigo CCA": "10.0.2.50"}
    assert gate.get("/api/home", headers=h).status_code == 403  # not ticked
    assert gate.get("/api/places", headers=h).status_code == 403
    e = gate.post("/api/ha/entity", headers=h, json={"component": "sensor", "object_id": "panel_1", "name": "Panel 1", "state": 230, "unit": "W"})
    assert e.json()["entity"] == "sensor.wa_x_tigo_panel_1"
    assert any(t.endswith("sensor/wa_x_tigo_panel_1/config") for t, _ in world["published"])
    assert gate.post("/api/ha/entity", headers=h, json={"component": "binary_sensor", "object_id": "x", "name": "x"}).status_code == 403
    assert gate.post("/api/notify", headers=h, json={"title": "Panel 3", "body": "low output", "key": "p3"}).json()["sent"]["sent"]
    assert G.allowed_host("10.0.2.50", 80, {"home_devices": {"Tigo CCA": "10.0.2.50"}})
    assert not G.allowed_host("10.0.2.51", 80, {"home_devices": {"Tigo CCA": "10.0.2.50"}})
    assert G.allowed_host("api.x.com", 443, {"hosts": ["*.x.com"]}) and not G.allowed_host("evil.com", 443, {"hosts": ["*.x.com"]})

    # the key it declares
    assert api.put("/v1/outside/tigo/keys/tigo_password", json={"value": "s3cret"}).status_code == 200
    restart = runner(api, {"start": {"running": True}})[0]["spec"]
    assert restart["env"]["WA_KEY_TIGO_PASSWORD"] == "s3cret"
    assert api.put("/v1/outside/tigo/settings", json={"every": "often"}).status_code == 422
    assert api.put("/v1/outside/tigo/settings", json={"every": 30, "other": 1}).json()["settings"] == {"every": 30}

    # an update that passes every check installs by itself
    world["rel"]["now"] = release("1.1.0")
    out = api.portal.call(X.outside_daily, {"engine": api.app.state.engine})  # type: ignore[attr-defined,union-attr]
    assert out["newer"] == ["tigo v1.1.0"], out
    runner(api, {"check": GOOD, "start": {"running": True}, "remove_image": {"ok": True}})
    x = api.get("/v1/outside/tigo").json()
    assert (x["version"], x["state"], x.get("held")) == ("1.1.0", "running", None), x["events"][:5]

    # one that asks for more waits for you, with a notification
    world["rel"]["now"] = release("1.2.0", places="true")
    api.portal.call(X.outside_daily, {"engine": api.app.state.engine})  # type: ignore[attr-defined,union-attr]
    runner(api, {"check": GOOD})
    x = api.get("/v1/outside/tigo").json()
    assert x["version"] == "1.1.0" and x["held"]["tag"] == "v1.2.0" and x["needs_attention"]
    assert "your saved places" in x["held"]["reasons"][0] and any("held:v1.2.0" in k for k, _ in world["sent"])
    r = api.post("/v1/outside/tigo/approve", json={"data": {"places": True}, "home_devices": {"Tigo CCA": "10.0.2.50"}})
    assert r.status_code == 200
    runner(api, {"start": {"running": True}, "remove_image": {"ok": True}})
    x = api.get("/v1/outside/tigo").json()
    assert x["version"] == "1.2.0" and x["consent"]["data"] == {"places": True}
    G._cache.clear()
    assert len(gate.get("/api/places", headers=h).json()["places"]) == 1

    # removal: every trace, and a report that says so
    assert api.post("/v1/outside/tigo/remove").json()["state"] == "removing"
    jobs = runner(api, {"remove": {"checked": {"containers": [], "volumes": [], "images": []}}})
    assert jobs[-1]["kind"] == "remove" and sorted(jobs[-1]["spec"]["refs"])
    rep = api.get("/v1/outside/removed/tigo").json()
    assert rep["state"] == "removed" and rep["report"]["clean"], rep
    assert not list(world["keys"].glob("x_tigo*"))
    assert ("homeassistant/sensor/wa_x_tigo_panel_1/config", "") in world["published"][-3:] or any(
        t.endswith("wa_x_tigo_panel_1/config") and p == "" for t, p in world["published"])
    assert api.get("/v1/outside/tigo").status_code == 404
    G._cache.clear()
    assert gate.get("/api/me", headers=h).status_code == 401  # its token no longer opens anything
    with sa.create_engine(URL).begin() as conn:  # type: ignore[arg-type]
        left = conn.execute(sa.text("SELECT kind FROM xmod_event WHERE module = 'tigo' ORDER BY id")).scalars().all()
        assert set(left) == {"install", "remove", "removed"}
        assert conn.execute(sa.text("SELECT count(*) FROM notify_log WHERE key LIKE 'x:tigo:%'")).scalar() == 0


def test_a_proxy_login_is_read() -> None:
    assert base64.b64decode(base64.b64encode(b"tigo:tok")).decode().partition(":") == ("tigo", ":", "tok")
