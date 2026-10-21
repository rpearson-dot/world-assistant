"""Backup API: make a backup, list them, download one. Restoring is deliberately command-line only."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app import backup
from app.auth import require_token

router = APIRouter(prefix="/v1/backups", tags=["backups"], dependencies=[Depends(require_token)])


@router.get("")
async def list_all() -> dict[str, Any]:
    items = await asyncio.to_thread(backup.list_backups)
    return {"folder": str(backup.backup_dir()), "keep": backup.keep_count(), "backups": items}


@router.post("", status_code=201)
async def create_one() -> dict[str, Any]:
    try:
        return await asyncio.to_thread(backup.create)
    except backup.BackupError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/{name}")
async def download(name: str) -> FileResponse:
    try:
        path = backup.path_for(name)
    except FileNotFoundError:
        raise HTTPException(404, "no such backup") from None
    return FileResponse(path, media_type="application/gzip", filename=path.name)
