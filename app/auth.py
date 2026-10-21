"""Bearer-token check for the /v1 API. The token lives in the api_token secret."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from app.config import get_settings


async def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = get_settings().api_token
    if not expected:
        # Fail closed: an unset token must never mean "open".
        raise HTTPException(status_code=503, detail="api_token secret is not set")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401, detail="invalid token", headers={"WWW-Authenticate": "Bearer"}
        )
