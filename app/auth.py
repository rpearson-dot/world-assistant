"""Bearer-token check for the /v1 API: the main token (the api_token secret), or a client's own token with only
the access it was given (app/clients.py).

Requests from the local network need no token, nor do the web pages' own fetches from a browser on the
tailnet (app/localnet.py says exactly what counts).
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request

from app.config import get_settings
from app.localnet import is_local, tailnet_page_fetch


async def require_token(request: Request, authorization: str | None = Header(default=None)) -> None:
    if is_local(request, request.method) or tailnet_page_fetch(request, request.method):
        request.state.client, request.state.client_access = (
            "home network" if is_local(request, request.method) else "web app (tailnet)"), "full"
        return
    expected = get_settings().api_token
    scheme, _, supplied = (authorization or "").partition(" ")
    supplied = supplied.strip() if scheme.lower() == "bearer" else ""
    if expected and supplied and secrets.compare_digest(supplied, expected):
        request.state.client, request.state.client_access = "main token", "full"
        return
    # a client's own token (app/clients.py): only what it is allowed
    from app.clients import allowed, lookup

    engine = getattr(request.app.state, "engine", None)
    c = await lookup(engine, supplied) if supplied and engine is not None else None
    if c is not None:
        request.state.client, request.state.client_access = c.name, c.access
        why = allowed(c, request.method, request.url.path)
        if why:
            raise HTTPException(status_code=403, detail=why)
        return
    if not expected:
        # Fail closed: an unset token must never mean "open" (beyond the local network, above).
        raise HTTPException(status_code=503, detail="api_token secret is not set")
    raise HTTPException(status_code=401, detail="invalid token", headers={"WWW-Authenticate": "Bearer"})
