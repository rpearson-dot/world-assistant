"""Bearer-token check for the /v1 API. The token lives in the api_token secret.

Requests from the local network need no token (app/localnet.py says exactly what counts as local).
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request

from app.config import get_settings
from app.localnet import is_local


async def require_token(request: Request, authorization: str | None = Header(default=None)) -> None:
    if is_local(request, request.method):
        return
    expected = get_settings().api_token
    if not expected:
        # Fail closed: an unset token must never mean "open" (beyond the local network, above).
        raise HTTPException(status_code=503, detail="api_token secret is not set")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401, detail="invalid token", headers={"WWW-Authenticate": "Bearer"}
        )
