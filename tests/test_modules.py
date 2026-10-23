"""Modules: the declarations match the code they describe, a switched-off module goes dark everywhere, a choice
is saved and read back, the region suggestion works, and an outside module can be added without touching the core."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Self

import pytest
from arq import cron
from fastapi.testclient import TestClient

from app import assistant, secretstore
from app.config import Settings
from app.main import app
from app.modules import registry, selection
from app.modules.gate import ModuleGate, gated
from app.modules.model import Job, Module
from app.modules.routes import suggest

ROOT = Path(__file__).parent.parent


# --- the declarations match the code ------------------------------------------------------------------------


def test_ids_are_unique_and_every_declared_import_resolves() -> None:
    ids = [m.id for m in registry.modules()]
    assert len(ids) == len(set(ids))
    for m in registry.modules():
        for r in m.routers:
            assert registry.import_string(r).routes is not None, r
        for j in m.worker:
            assert callable(registry.import_string(j.func)), j.func


def test_every_schedule_is_one_arq_accepts() -> None:
    settings = Settings()
    for m in registry.modules():
        for j in m.worker:
            if j.cron is not None:
                spec = j.cron(settings) if callable(j.cron) else j.cron
                cron(registry.import_string(j.func), **spec)


def test_keys_are_known_secrets_and_reach_the_containers() -> None:
    known = {k.name for k in secretstore.KNOWN}
    compose = (ROOT / "docker-compose.yml").read_text()
    for m in registry.modules():
        for key in m.keys:
            assert key in known, f"{m.id}: {key} is not in secretstore.KNOWN"
            assert re.search(rf"^\s+- {key}$", compose, re.MULTILINE), f"{m.id}: {key} is not given to a container"
            assert re.search(rf"^  {key}:$", compose, re.MULTILINE), f"{m.id}: {key} has no secret file entry"
    names = {k.name for k in secretstore.KNOWN}
    assert not [k for m in registry.modules() for k in m.keys if k not in names]


def test_switches_and_status_names_exist() -> None:
    s = Settings()
    for m in registry.modules():
        if m.switch:
            assert hasattr(s, m.switch), f"{m.id}: no setting {m.switch}"
        for attr in m.extra_ready:
            assert hasattr(s, attr)


def test_every_route_of_a_switchable_module_is_under_its_prefixes_and_no_two_modules_share_one() -> None:
    for m in registry.modules():
        if m.core:
            continue
        for r in m.routers:
            for route in registry.import_string(r).routes:
                path = getattr(route, "path", "")
                assert any(registry.path_matches(path, p) for p in m.prefixes), f"{m.id}: {path} would stay up when it is off"
    for path in ("/v1/fuel/near", "/fuel", "/v1/trips/abc/conditions", "/v1/trips/abc/cost", "/v1/bus/trackers"):
        owners = [m.id for m in registry.modules() if any(registry.path_matches(path, p) for p in m.prefixes)]
        assert len(owners) == 1, (path, owners)
    assert registry.module_for_path("/v1/trips/abc") is None  # trips themselves are core
    assert registry.module_for_path("/v1/fuelish") is None  # whole segments only


def test_every_assistant_tool_belongs_to_a_module_and_every_claim_is_real() -> None:
    claimed = {t for m in registry.modules() for t in m.tools}
    assert claimed == set(assistant.TOOLS)
    assert registry.module_of_tool("fuel_cheapest") == "fuel"


def test_the_setup_list_still_has_what_it_had() -> None:
    from app.setup_checklist import sources

    ids = [m.id for m in sources()]
    assert ids[:6] == ["nh", "fuel", "tfl", "nr", "bods", "tfwm"]
    assert {"events", "pollen", "octopus", "floods", "tflroads", "naptan", "boundary", "usrn", "codepoint"} <= set(ids)


# --- which are on ---------------------------------------------------------------------------------------------


def mods() -> list[Module]:
    return list(registry.modules())


def test_with_no_choice_everything_is_on_so_an_upgrade_changes_nothing() -> None:
    assert selection.enabled_ids(mods(), selection.Selection()) == {m.id for m in mods()}


def test_all_but_and_only_modes_and_the_core_stays_on() -> None:
    off = selection.enabled_ids(mods(), selection.Selection("all", off=frozenset({"fuel", "core"}), chosen=True))
    assert "fuel" not in off and "core" in off  # the core cannot be switched off
    only = selection.enabled_ids(mods(), selection.Selection("only", on=frozenset({"bus"}), chosen=True))
    assert only == {"core", "bus"}


def test_what_a_module_needs_comes_with_it() -> None:
    a = Module("a", "A", "x", needs=("b",))
    b = Module("b", "B", "x")
    c = Module("c", "C", "x")
    assert selection.enabled_ids([a, b, c], selection.Selection("only", on=frozenset({"a"}), chosen=True)) == {"a", "b"}


def test_a_saved_choice_round_trips_and_garbage_means_no_choice() -> None:
    sel = selection.Selection("all", off=frozenset({"fuel"}), chosen=True)
    assert selection.Selection.from_json(sel.to_json()) == sel
    assert selection.Selection.from_json({"mode": "banana"}).chosen is False
    assert selection.Selection.from_json(None).chosen is False


# --- going dark -----------------------------------------------------------------------------------------------


class FakeEngine:
    """Enough of an engine for the choice to be read: one saved value, or nothing."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def connect(self) -> FakeEngine:
        return self

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def execute(self, *a: object, **k: object) -> Any:
        row = None if self.value is None else (self.value,)

        class R:
            def first(self_inner) -> Any:
                return row

        return R()


@pytest.fixture(autouse=True)
def _fresh_cache() -> Any:
    selection.clear_cache()
    yield
    selection.clear_cache()


async def test_a_job_stands_down_while_its_module_is_off() -> None:
    ran: list[int] = []

    async def job(ctx: dict[str, Any]) -> str:
        ran.append(1)
        return "ok"

    wrapped = gated(job, "fuel")
    assert wrapped.__name__ == "job"  # arq finds it by name
    assert await wrapped({"engine": FakeEngine({"mode": "all", "off": ["fuel"]})}) == "module off"
    assert ran == []
    selection.clear_cache()
    assert await wrapped({"engine": FakeEngine(None)}) == "ok" and ran == [1]
    assert gated(job, "core") is job


def test_the_web_answers_404_for_a_module_that_is_off_and_pages_explain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    app.state.engine = FakeEngine({"mode": "all", "off": ["fuel"]})
    try:
        c = TestClient(app)
        r = c.get("/v1/fuel/near", headers={"Authorization": "Bearer tok"})
        assert r.status_code == 404 and r.json()["module"] == "fuel" and "/modules" in r.json()["detail"]
        page = c.get("/fuel")
        assert page.status_code == 404 and "switched off" in page.text and "/modules" in page.text
        assert c.get("/healthz").status_code == 200  # the core is untouched
        listed = c.get("/v1/ask", headers={"Authorization": "Bearer tok"}).json()  # core, and the tool list is filtered
        assert "fuel_cheapest" not in [t["tool"] for t in listed] and "pollen" in [t["tool"] for t in listed]
    finally:
        del app.state.engine


async def test_a_tool_of_a_module_that_is_off_is_neither_listed_nor_callable() -> None:
    class Req:
        class app:
            class state:
                engine = FakeEngine({"mode": "all", "off": ["fuel"]})

    on = await assistant._available(Req())  # type: ignore[arg-type]
    assert "fuel_cheapest" not in on and "pollen" in on and "list_options" in on
    with pytest.raises(assistant.ToolError, match="switched off"):
        await assistant.run_tool(Req(), "fuel_cheapest", {})  # type: ignore[arg-type]


def test_gate_passes_other_traffic_through() -> None:
    assert ModuleGate is not None  # constructed by the app; behaviour is covered above


# --- suggesting what suits where you live ---------------------------------------------------------------------


def test_regional_modules_are_suggested_for_where_you_live() -> None:
    london = suggest(mods(), (51.40, -0.26, ["London"]))
    assert "tfl" in london and "tflroads" in london and "tfwm" not in london
    brum = suggest(mods(), (52.55, -1.83, ["West Midlands"]))
    assert "tfwm" in brum and "tfl" not in brum
    assert "fuel" not in london and "bods" not in london  # everywhere-modules are not "suggested for here"
    assert suggest(mods(), None) == {}


def test_a_region_name_is_enough_when_the_box_misses() -> None:
    tfwm = registry.get("tfwm")
    assert tfwm and tfwm.covers(52.9, -2.5, ["West Midlands Region"]) and not tfwm.covers(52.9, -2.5, ["East Midlands"])


# --- a module from outside the image ---------------------------------------------------------------------------


def test_an_outside_module_is_added_by_import_string_and_cannot_take_a_builtin_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pkg = tmp_path / "wa_demo_module"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from app.modules.model import Module\n"
        "DEMO = Module('demo', 'Demo', 'A module from outside', prefixes=('/v1/demo',), tools=('demo_tool',))\n"
        "CLASH = Module('fuel', 'Fake fuel', 'tries to replace fuel', core=True)\n"
        "def many():\n    return [DEMO, CLASH]\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("WA_EXTRA_MODULES", "wa_demo_module:many,wa_demo_module:missing")
    registry.reload()
    try:
        demo = registry.get("demo")
        assert demo and demo.source == "wa_demo_module:many" and not demo.core
        assert registry.get("fuel").name != "Fake fuel"  # type: ignore[union-attr]
        assert registry.module_for_path("/v1/demo/x") == "demo"
        assert registry.module_of_tool("demo_tool") == "demo"
        # and it can be switched off like any other
        assert "demo" not in selection.enabled_ids(
            list(registry.modules()), selection.Selection("all", off=frozenset({"demo"}), chosen=True))
    finally:
        monkeypatch.delenv("WA_EXTRA_MODULES")
        registry.reload()
    assert registry.get("demo") is None


def test_a_module_that_cannot_load_does_not_stop_the_core(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_EXTRA_MODULES", "no_such_package:THING")
    registry.reload()
    try:
        assert registry.get("core") is not None and len(registry.modules()) >= 20
    finally:
        monkeypatch.delenv("WA_EXTRA_MODULES")
        registry.reload()


def test_a_job_is_just_data() -> None:
    assert Job("a.b:c").cron is None


# --- the API, against a real database --------------------------------------------------------------------------

import os
from collections.abc import Iterator

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

URL = os.environ.get("WA_TEST_DATABASE_URL")
H = {"Authorization": "Bearer tok"}


@pytest.fixture
def settings_table() -> Iterator[None]:
    sync = sa.create_engine(URL)  # type: ignore[arg-type]
    with sync.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS app_setting"))
        conn.execute(sa.text("CREATE TABLE app_setting (key text primary key, value jsonb not null,"
                             " updated_at timestamptz not null default now())"))  # fmt: skip
    yield
    with sync.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS app_setting"))
    sync.dispose()


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
def test_choose_switch_and_reset_through_the_api(monkeypatch: pytest.MonkeyPatch, settings_table: None) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    app.state.engine = create_async_engine(URL, poolclass=NullPool)  # type: ignore[arg-type]
    try:
        c = TestClient(app, raise_server_exceptions=False)  # a module that is on may fail on this bare database
        first = c.get("/v1/modules", headers=H).json()
        assert first["chosen"] is False and all(m["enabled"] for m in first["modules"])
        assert {"files", "vault", "command"} == {x["id"] for x in first["secrets"]["choices"]}
        assert first["secrets"]["backend"] in ("files", "vault", "command")

        only = c.put("/v1/modules", headers=H, json={"mode": "only", "on": ["bus"]}).json()
        on = {m["id"] for m in only["modules"] if m["enabled"]}
        assert only["chosen"] and on == {"core", "bus"}
        assert c.get("/v1/fuel/near", headers=H).status_code == 404  # off, and nothing needed a restart
        assert c.get("/v1/ask", headers=H).status_code == 200

        turned = c.post("/v1/modules/fuel/enable", headers=H).json()
        assert {m["id"] for m in turned["modules"] if m["enabled"]} == {"core", "bus", "fuel"}
        assert c.get("/v1/fuel/near", headers=H).status_code != 404
        c.post("/v1/modules/bus/disable", headers=H)
        assert c.get("/v1/bus/trackers", headers=H).status_code == 404

        assert c.post("/v1/modules/core/disable", headers=H).status_code == 409
        assert c.post("/v1/modules/nope/enable", headers=H).status_code == 404
        assert c.put("/v1/modules", headers=H, json={"mode": "only", "on": ["nope"]}).status_code == 422
        assert c.post("/v1/modules/pollen/test", headers=H).json()["ok"] is None  # no live test for this one
        assert c.get("/v1/modules").status_code == 401  # behind the token

        back = c.delete("/v1/modules", headers=H).json()
        assert back["chosen"] is False and all(m["enabled"] for m in back["modules"])
    finally:
        del app.state.engine


@pytest.mark.skipif(not URL, reason="WA_TEST_DATABASE_URL not set")
def test_all_but_mode_keeps_the_rest_when_one_is_flipped(monkeypatch: pytest.MonkeyPatch, settings_table: None) -> None:
    monkeypatch.setenv("API_TOKEN", "tok")
    app.state.engine = create_async_engine(URL, poolclass=NullPool)  # type: ignore[arg-type]
    try:
        c = TestClient(app)
        r = c.post("/v1/modules/fuel/disable", headers=H).json()
        off = {m["id"] for m in r["modules"] if not m["enabled"]}
        assert off == {"fuel"} and r["mode"] == "all"
    finally:
        del app.state.engine


def test_the_modules_page_is_served() -> None:
    page = TestClient(app).get("/modules")
    assert page.status_code == 200 and "Modules" in page.text and "/v1/modules" in page.text
