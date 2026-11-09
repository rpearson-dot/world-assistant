"""The automatic-update switch (on the status page), the "Update now" button, and the record of the last update attempt.

The api cannot restart containers, and should not be able to. The switch is only a stored flag.
The updater container (scripts/updater.sh runs scripts/auto-deploy.sh nightly) reads it, does the update, and posts the
outcome back here so the status page can show it.

"Update now" is a stored request in the same way. The updater container (scripts/updater.sh: auto-deploy.sh
--if-requested, every minute) sees it, claims it, and runs the same update as --now: signature check, backup, page and endpoint check,
and rollback. Its report clears the request. A claim more than STALE_S old with no report counts as not picked up, so
a job that died part-way does not leave the button stuck.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.version import COMMIT, VERSION

router = APIRouter(prefix="/v1/auto-deploy", tags=["auto-deploy"], dependencies=[Depends(require_token)])

SWITCH = "auto_deploy"
LAST = "auto_deploy_last"
ASK = "auto_deploy_request"
STALE_S = 45 * 60  # an update that has not reported in this long has died


class SwitchIn(BaseModel):
    enabled: bool


class ReportIn(BaseModel):
    # updated: now running the new version. unchanged: nothing newer. refused: not applied, and why.
    # rolled_back: the update failed its checks and the previous version is running again.
    # failed: could not update and could not confirm the old version either: needs a look.
    result: Literal["updated", "unchanged", "refused", "rolled_back", "failed"]
    message: str = Field(default="", max_length=1000)
    from_version: str | None = Field(default=None, max_length=80)
    to_version: str | None = Field(default=None, max_length=80)


async def _get(request: Request, key: str) -> dict[str, Any] | None:
    async with request.app.state.engine.connect() as conn:
        row = (await conn.execute(text("SELECT value, updated_at FROM app_setting WHERE key = :k"), {"k": key})).first()
    if row is None:
        return None
    value = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return {**value, "at": row[1].isoformat()}


async def _put(request: Request, key: str, value: dict[str, Any]) -> None:
    async with request.app.state.engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                """
            ),
            {"k": key, "v": json.dumps(value)},
        )


def _ask_view(ask: dict[str, Any] | None, now: datetime) -> dict[str, Any] | None:
    """The open "Update now" request, if any: asked when, picked up when. None once reported or given up on."""
    if not ask or not ask.get("asked"):
        return None
    claimed = ask.get("claimed")
    if claimed and now - datetime.fromisoformat(claimed) > timedelta(seconds=STALE_S):
        return None
    return {"asked": ask["asked"], "claimed": claimed}


async def state(request: Request) -> dict[str, Any]:
    switch = await _get(request, SWITCH)
    last = await _get(request, LAST)
    ask = _ask_view(await _get(request, ASK), datetime.now(UTC))
    return {
        "enabled": bool(switch and switch.get("enabled")),
        "changed_at": switch["at"] if switch else None,
        "running": {"version": VERSION, "commit": COMMIT},
        "last_run": last,
        "request": ask,
        "pending": bool(ask and not ask["claimed"]),  # for the server's job: an update is asked for and not yet taken
    }


@router.get("")
async def get_state(request: Request) -> dict[str, Any]:
    return await state(request)


@router.put("")
async def set_switch(body: SwitchIn, request: Request) -> dict[str, Any]:
    await _put(request, SWITCH, {"enabled": body.enabled})
    return await state(request)


@router.post("/request", status_code=202)
async def ask_now(request: Request) -> dict[str, Any]:
    """Update now: the server's every-minute job picks it up. Asking again while one is open changes nothing."""
    if not (await state(request))["request"]:
        await _put(request, ASK, {"asked": datetime.now(UTC).isoformat(), "claimed": None})
    return await state(request)


@router.delete("/request")
async def cancel(request: Request) -> dict[str, Any]:
    """Take back an "Update now" the server has not picked up yet."""
    s = await state(request)
    if s["request"] and s["request"]["claimed"]:
        raise HTTPException(409, "the server has already started this update")
    await _put(request, ASK, {})
    return await state(request)


@router.post("/request/claim")
async def claim(request: Request) -> dict[str, Any]:
    """The server's job takes the open request (409 when there is none to take, or another job has it)."""
    s = await state(request)
    if not s["pending"]:
        raise HTTPException(409, "no update is waiting")
    await _put(request, ASK, {**s["request"], "claimed": datetime.now(UTC).isoformat()})
    return await state(request)


@router.post("/report", status_code=201)
async def report(body: ReportIn, request: Request) -> dict[str, Any]:
    await _put(request, LAST, {**body.model_dump(), "reported": datetime.now(UTC).isoformat()})
    await _put(request, ASK, {})  # whatever happened, the asked-for update is over
    return await state(request)
