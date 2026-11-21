"""The capability manifest (GET /v1/capabilities): everything World Assistant can do, per module, as typed
capabilities an iPhone (or any client) can turn into App Intents for Siri.

It is built from the module declarations — their tools, tabs and Home Assistant feeds — and each tool's own
App-Intent metadata (title, summary, phrases, kind), so adding a module adds its capabilities automatically, and a
tool without that metadata fails the enforcement test. The server is the source of truth; the app only chooses what
to show and what to say.

A capability is one of:
- `query`  — answers a question (an assistant tool that reads).
- `action` — changes something (an assistant tool that acts; `siri_safe: false` should confirm first).
- `page`   — a UI page (a tab).
- `feed`   — what the module publishes to Home Assistant (entities, notifications).

Each carries `consume` (every way to call it) and, for query/action, an `intent` block the app maps to an App Intent.
The response carries a `version` (a stable hash of the set) and supports ETag / If-None-Match, so the app's sync is a
cheap 304 when nothing has changed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from app.auth import require_token

router = APIRouter(prefix="/v1/capabilities", tags=["capabilities"], dependencies=[Depends(require_token)])

# Parameter names whose options the app can list from a live endpoint, so "add bread to {list}" offers the real lists.
_VALUES_FROM = {"place": "list_options", "vehicle": "list_options", "trip": "list_options", "board": "list_options",
                "board_id": "list_options", "tracker": "list_options", "list": "list_options", "board_name": "list_options"}  # fmt: skip


def _parameters(schema: dict[str, Any]) -> list[dict[str, Any]]:
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    out = []
    for name, spec in props.items():
        p: dict[str, Any] = {"name": name, "type": (spec or {}).get("type", "string"), "optional": name not in required}
        if (spec or {}).get("description"):
            p["description"] = spec["description"]
        if name in _VALUES_FROM:
            p["values_from"] = _VALUES_FROM[name]
        out.append(p)
    return out


def _tool_capability(tool: Any) -> dict[str, Any]:
    kind = tool.kind if tool.kind in ("query", "action") else "query"
    return {
        "kind": kind,
        "name": tool.name,
        "title": tool.title,
        "summary": tool.summary,
        "consume": {"mcp_tool": tool.name, "ask": "/v1/ask"},
        "intent": {
            "title": tool.title,
            "summary": tool.summary,
            "parameters": _parameters(tool.schema),
            "result": "spoken",
            "phrases": list(tool.phrases),
            "siri_safe": bool(tool.siri_safe),
        },
    }


def build() -> dict[str, Any]:
    """The manifest, without `generated_at` (so the version hash is stable across calls)."""
    from app.assistant import TOOLS
    from app.modules import registry

    modules = []
    for m in registry.modules():
        caps: list[dict[str, Any]] = []
        for name in m.tools:
            t = TOOLS.get(name)
            if t is not None:
                caps.append(_tool_capability(t))
        for _section, _pid, path, label in m.tabs:
            caps.append({"kind": "page", "title": label, "summary": m.unlocks, "consume": {"page_url": path}})
        for g in m.gives:
            caps.append({"kind": "feed", "title": g, "summary": "", "consume": {"ha": g}})
        if caps:
            modules.append({"id": m.id, "name": m.name, "category": m.category, "capabilities": caps})
    modules.sort(key=lambda x: x["id"])
    return {"modules": modules}


def _version(manifest: dict[str, Any]) -> str:
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


@router.get("")
async def capabilities(request: Request, response: Response) -> Any:
    manifest = build()
    version = _version(manifest)
    etag = f'"{version}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    body = {"version": version, "generated_at": datetime.now(UTC).isoformat(), **manifest}
    return JSONResponse(body, headers={"ETag": etag, "Cache-Control": "no-cache"})
