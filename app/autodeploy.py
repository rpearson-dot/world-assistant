"""The automatic-update switch (on the status page) and the record of the last update attempt.

The api cannot restart containers, and should not be able to. The switch is only a stored flag.
A job on the server (scripts/auto-deploy.sh, run nightly) reads it, does the update, and posts the
outcome back here so the status page can show it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.version import COMMIT, VERSION

router = APIRouter(prefix="/v1/auto-deploy", tags=["auto-deploy"], dependencies=[Depends(require_token)])

SWITCH = "auto_deploy"
LAST = "auto_deploy_last"


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


async def state(request: Request) -> dict[str, Any]:
    switch = await _get(request, SWITCH)
    last = await _get(request, LAST)
    return {
        "enabled": bool(switch and switch.get("enabled")),
        "changed_at": switch["at"] if switch else None,
        "running": {"version": VERSION, "commit": COMMIT},
        "last_run": last,
    }


@router.get("")
async def get_state(request: Request) -> dict[str, Any]:
    return await state(request)


@router.put("")
async def set_switch(body: SwitchIn, request: Request) -> dict[str, Any]:
    await _put(request, SWITCH, {"enabled": body.enabled})
    return await state(request)


@router.post("/report", status_code=201)
async def report(body: ReportIn, request: Request) -> dict[str, Any]:
    await _put(request, LAST, {**body.model_dump(), "reported": datetime.now(UTC).isoformat()})
    return await state(request)
