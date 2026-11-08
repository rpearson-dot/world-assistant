"""The server's default look: the theme and mode a device shows until it chooses its own (Settings, Look).

    GET /v1/settings/look      {"theme", "mode"}
    PUT /v1/settings/look      set it: every device that has not chosen its own follows from its next page

Kept in app_setting; read when the API starts (load) and put into each page's head script by pagestyle.themed.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from app import themes
from app.auth import require_token

log = logging.getLogger("wa.look")
router = APIRouter(prefix="/v1/settings/look", tags=["settings"], dependencies=[Depends(require_token)])
KEY = "look_default"


async def load(engine: Any) -> None:
    try:
        async with engine.connect() as c:
            v = (await c.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": KEY})).scalar()
    except Exception:  # noqa: BLE001 - no database yet: Standard, following the device
        log.info("the default look could not be read: using Standard")
        return
    v = json.loads(v) if isinstance(v, str) else v
    if isinstance(v, dict) and v.get("theme") in themes.THEMES and v.get("mode") in themes.MODES:
        themes.SERVER.update(theme=v["theme"], mode=v["mode"])


class LookIn(BaseModel):
    theme: str
    mode: str = "auto"


@router.get("")
async def get_look() -> dict[str, Any]:
    return dict(themes.SERVER)


@router.put("")
async def put_look(body: LookIn, request: Request) -> dict[str, Any]:
    if body.theme not in themes.THEMES or body.mode not in themes.MODES:
        raise HTTPException(422, f"theme is one of {', '.join(themes.THEMES)}; mode one of {', '.join(themes.MODES)}")
    async with request.app.state.engine.begin() as c:
        await c.execute(text("INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
                             "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
                        {"k": KEY, "v": json.dumps({"theme": body.theme, "mode": body.mode})})  # fmt: skip
    themes.SERVER.update(theme=body.theme, mode=body.mode)
    return dict(themes.SERVER)
