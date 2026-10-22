"""The container icons for Unraid's Docker tab (served without a token: they are just pictures)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["icons"])
_DIR = Path(__file__).parent / "icons"
NAMES = {"world-assistant", "worker", "database", "cache", "routing", "vault", "graph"}


@router.get("/icons/{name}.png", include_in_schema=False)
async def icon(name: str) -> FileResponse:
    if name not in NAMES or not (_DIR / f"{name}.png").is_file():
        raise HTTPException(404, "no such icon")
    return FileResponse(_DIR / f"{name}.png", media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
