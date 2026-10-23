"""Modules: see what there is, switch modules on and off, test a key, and get a suggestion for where you live.

API under /v1/modules; the page is /modules. Switching a module takes effect within seconds, with no restart.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import secretstore
from app.auth import require_token
from app.config import SECRETS_DIR, get_settings
from app.modules import keytest, registry, selection
from app.modules.model import Module
from app.pagestyle import themed
from app.setup_checklist import judge

router = APIRouter(prefix="/v1/modules", tags=["modules"], dependencies=[Depends(require_token)])
page_router = APIRouter(include_in_schema=False)


async def home_point(engine: Any) -> tuple[float, float, list[str]] | None:
    """(lat, lon, names of the regions it is in) for the home place; the names only when boundaries are loaded."""
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text(
                "SELECT lat, lon FROM place WHERE kind = 'home' OR id = 'home' ORDER BY (kind = 'home') DESC LIMIT 1"))).first()
            if row is None or row[0] is None:
                return None
            regions: list[str] = []
            try:
                regions = [r[0] for r in await conn.execute(text(
                    "SELECT name FROM boundary WHERE kind = 'region' "
                    "AND ST_Intersects(geom, ST_SetSRID(ST_MakePoint(:lo, :la), 4326))"), {"la": row[0], "lo": row[1]})]
            except Exception:  # noqa: BLE001 - no boundaries loaded: coverage falls back to boxes
                regions = []
            return float(row[0]), float(row[1]), regions
    except Exception:  # noqa: BLE001
        return None


def suggest(modules: list[Module], point: tuple[float, float, list[str]] | None) -> dict[str, str]:
    """{module id: why} for the modules that are specific to a region and cover where you live."""
    if point is None:
        return {}
    lat, lon, regions = point
    out: dict[str, str] = {}
    for m in modules:
        if (m.coverage or m.area is not None) and m.covers(lat, lon, regions):
            out[m.id] = f"covers where you live ({', '.join(regions) or 'by position'})"
    return out


def describe(m: Module, s: Any, runs: dict[str, dict[str, Any]], on: set[str], why: dict[str, str]) -> dict[str, Any]:
    row = judge(m, s, runs)
    return {
        "id": m.id, "name": m.name, "unlocks": m.unlocks, "category": m.category, "core": m.core,
        "enabled": m.id in on, "state": row["state"], "keys": list(m.keys), "missing_keys": row["missing_keys"],
        "any_key": m.any_key, "key_optional": m.key_optional, "link": m.link, "note": m.note,
        "services": list(m.services), "needs": list(m.needs), "coverage": list(m.coverage),
        "suggested": why.get(m.id), "source": m.source, "last_ok": row.get("last_ok"), "error": row.get("error"),
        "testable": m.id in keytest.TESTS,
    }  # fmt: skip


async def _runs(engine: Any) -> dict[str, dict[str, Any]]:
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT DISTINCT ON (job) job, started_at, finished_at, status, error FROM job_run "
                "ORDER BY job, started_at DESC"))).mappings().all()  # fmt: skip
        return {r["job"]: dict(r) for r in rows}
    except Exception:  # noqa: BLE001
        return {}


def secrets_view() -> dict[str, Any]:
    backend = secretstore.backend_name()
    rows = secretstore.report(SECRETS_DIR)
    return {
        "backend": backend,
        "choices": [
            {"id": "files", "name": "Files", "how": "Secret files in the secrets folder (the default). Nothing to install."},
            {"id": "vault", "name": "OpenBao or Vault",
             "how": "Run the bundled OpenBao with  bash scripts/openbao.sh setup,  or point WA_VAULT_ADDR at your own. "
                    "Set WA_SECRETS_BACKEND=vault in .env and restart."},
            {"id": "command", "name": "Your own key manager",
             "how": "Any tool with a command line (1Password, Bitwarden, pass, sops, AWS, Azure, Google): set "
                    "WA_SECRETS_BACKEND=command and WA_SECRETS_COMMAND to a command that prints the value for {name}."},
        ],
        "sources": {k: sum(1 for r in rows if r["source"] == k) for k in sorted({r["source"] for r in rows})},
    }


@router.get("")
async def list_modules(request: Request) -> dict[str, Any]:
    engine = request.app.state.engine
    s = get_settings()
    mods = list(registry.modules())
    sel = await selection.load(engine, fresh=True)
    on = selection.enabled_ids(mods, sel)
    why = suggest(mods, await home_point(engine))
    runs = await _runs(engine)
    return {"chosen": sel.chosen, "mode": sel.mode,
            "modules": [describe(m, s, runs, on, why) for m in mods], "secrets": secrets_view()}  # fmt: skip


class ChoiceIn(BaseModel):
    mode: Literal["all", "only"]
    on: list[str] = Field(default_factory=list, max_length=100)
    off: list[str] = Field(default_factory=list, max_length=100)


def _known(ids: list[str]) -> list[str]:
    have = {m.id for m in registry.modules()}
    bad = [i for i in ids if i not in have]
    if bad:
        raise HTTPException(422, f"unknown module(s): {', '.join(bad[:5])}")
    return ids


@router.put("")
async def choose(body: ChoiceIn, request: Request) -> dict[str, Any]:
    """Save the choice: everything except `off`, or only `on` (plus what those need, plus the core)."""
    sel = selection.Selection(mode=body.mode, on=frozenset(_known(body.on)), off=frozenset(_known(body.off)), chosen=True)
    await selection.save(request.app.state.engine, sel)
    return await list_modules(request)


@router.delete("")
async def forget(request: Request) -> dict[str, Any]:
    """Back to the default: every module on."""
    await selection.reset(request.app.state.engine)
    return await list_modules(request)


async def _flip(module_id: str, request: Request, turn_on: bool) -> dict[str, Any]:
    m = registry.get(module_id)
    if m is None:
        raise HTTPException(404, "no such module")
    if m.core:
        raise HTTPException(409, "the core cannot be switched off")
    mods = list(registry.modules())
    sel = await selection.load(request.app.state.engine, fresh=True)
    on = selection.enabled_ids(mods, sel)
    on = (on | {module_id}) if turn_on else (on - {module_id})
    if sel.mode == "only" and sel.chosen:
        new = selection.Selection("only", on=frozenset(on), chosen=True)
    else:
        new = selection.Selection("all", off=frozenset(x.id for x in mods if x.id not in on and not x.core), chosen=True)
    await selection.save(request.app.state.engine, new)
    return await list_modules(request)


@router.post("/{module_id}/enable")
async def enable(module_id: str, request: Request) -> dict[str, Any]:
    return await _flip(module_id, request, True)


@router.post("/{module_id}/disable")
async def disable(module_id: str, request: Request) -> dict[str, Any]:
    return await _flip(module_id, request, False)


@router.post("/{module_id}/test")
async def test_key(module_id: str) -> dict[str, Any]:
    """Try the module's key with one small live call. Never returns the key."""
    if registry.get(module_id) is None:
        raise HTTPException(404, "no such module")
    return await keytest.run(module_id, get_settings())


@page_router.get("/modules", response_class=HTMLResponse)
async def modules_page(request: Request) -> str:
    from app.modules.page import PAGE

    return themed(PAGE, "settings", request)
