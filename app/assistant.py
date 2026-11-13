"""Question answering: one set of tools, served to MCP clients (/mcp) and to plain HTTP callers
such as Siri Shortcuts (/v1/ask). Every tool returns a short sentence that reads well aloud,
plus the structured data behind it.

MCP is spoken here directly (JSON-RPC over HTTP POST, stateless) so no extra dependency is
needed. Authentication is the same bearer token as the rest of the API.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from app.auth import require_token
from app.version import VERSION

log = logging.getLogger("wa.assistant")
router = APIRouter(dependencies=[Depends(require_token)])

from app.assistant_base import Tool, ToolError, _ids


async def list_options(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    queries = {
        "places": "SELECT id FROM place ORDER BY id", "vehicles": "SELECT id FROM vehicle ORDER BY id",
        "trips": "SELECT id FROM trip ORDER BY id", "train_boards": "SELECT id FROM tfl_board ORDER BY id",
        "bus_trackers": "SELECT id FROM bus_tracker ORDER BY id",
    }  # fmt: skip
    data: dict[str, list[str]] = {}
    async with request.app.state.engine.connect() as conn:
        for name, sql in queries.items():
            data[name] = _ids(await conn.execute(text(sql)))
    parts = [
        f"{len(v)} {k.replace('_', ' ')}" + (f" ({', '.join(v)})" if v else "")
        for k, v in data.items()
    ]
    return {"text": "You have " + "; ".join(parts) + ".", "data": data}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("list_options", "List the saved places, vehicles, trips, train boards and bus trackers (their ids).",
         {"type": "object", "properties": {}}, list_options),
]}  # fmt: skip

# The tools in app/assistant_more.py (shopping, floods, roads, buses, stops, the graph, adding places and trips).
from app.assistant_more import MORE
from app.modules import registry as _registry

TOOLS.update({t.name: t for t in MORE})
# each module's own tools (provides "assistant.tools" on its declaration)
for _module, _tools in _registry.provided("assistant.tools"):
    TOOLS.update({t.name: t for t in _tools})


async def _available(request: Request) -> set[str]:
    """Tool names whose module is on. A tool no module claims is always available."""
    from app.modules import registry
    from app.modules.gate import enabled

    try:
        on = await enabled(request.app.state.engine)
    except Exception:  # noqa: BLE001 - the choice must not take the assistant down
        return set(TOOLS)
    return {n for n in TOOLS if (registry.module_of_tool(n) or "core") in on}


async def run_tool(request: Request, name: str, args: dict[str, Any]) -> dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"Unknown tool {name!r}.")
    if name not in await _available(request):
        raise ToolError(f"The module behind {name!r} is switched off. Turn it on at /modules.")
    try:
        return await tool.run(request, args)
    except (ValueError, TypeError) as exc:
        raise ToolError(f"Bad arguments: {exc}") from exc


# ---- plain HTTP, for Siri Shortcuts and scripts --------------------------------------------


@router.get("/v1/ask")
async def ask_index(request: Request) -> list[dict[str, Any]]:
    on = await _available(request)
    return [{"tool": t.name, "description": t.description, "arguments": t.schema.get("properties", {})}
            for t in TOOLS.values() if t.name in on]  # fmt: skip


@router.post("/v1/ask/{name}", response_model=None)
async def ask(
    request: Request,
    name: str,
    args: Annotated[dict[str, Any] | None, Body()] = None,
    format: str = "json",
) -> Any:
    """POST the arguments as JSON. `?format=text` returns just the sentence, ready to speak."""
    try:
        result = await run_tool(request, name, args or {})
    except ToolError as exc:
        if format == "text":
            return PlainTextResponse(str(exc), status_code=422)
        raise HTTPException(422, str(exc)) from exc
    return PlainTextResponse(result["text"]) if format == "text" else result


# ---- MCP over HTTP (JSON-RPC 2.0, stateless) -------------------------------------------------

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


def _rpc_error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


async def handle_rpc(request: Request, msg: Any) -> dict[str, Any] | None:
    """One JSON-RPC message in, one response out (None for notifications)."""
    if (
        not isinstance(msg, dict)
        or msg.get("jsonrpc") != "2.0"
        or not isinstance(msg.get("method"), str)
    ):
        return _rpc_error(
            msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request"
        )
    rid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if "id" not in msg:  # notification: nothing to answer
        return None
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "world-assistant", "version": VERSION},
            "instructions": "Personal assistant for getting around (fuel, trains, buses, roads, floods, EV chargers), "
                            "energy, pollen, events, power cuts and the family shopping list. Call list_options first "
                            "to see saved ids. Some tools act: add_to_list, price_list, send_to_basket (fills a "
                            "basket, never orders or pays), tick_off_order, clear_list, add_place and add_trip.",
        }}  # fmt: skip
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [
            {"name": t.name, "description": t.description, "inputSchema": t.schema}
            for t in TOOLS.values() if t.name in await _available(request)]}}  # fmt: skip
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if not isinstance(name, str) or name not in TOOLS or not isinstance(args, dict):
            return _rpc_error(rid, -32602, "Unknown tool or bad arguments")
        try:
            out = await run_tool(request, name, args)
            content = [{"type": "text", "text": out["text"]}]
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": content, "structuredContent": out["data"],
                                                           "isError": False}}  # fmt: skip
        except ToolError as exc:
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": str(exc)}],
                                                           "isError": True}}  # fmt: skip
        except Exception:
            log.exception("tool %s failed", name)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "That failed on the server; check the logs."}],
                "isError": True}}  # fmt: skip
    return _rpc_error(rid, -32601, f"Method not found: {method}")


@router.post("/mcp", response_model=None)
async def mcp_endpoint(request: Request) -> Any:
    try:
        body = await request.json()
    except ValueError:
        return _rpc_error(None, -32700, "Parse error")
    if isinstance(body, list):
        replies = [r for m in body[:20] if (r := await handle_rpc(request, m)) is not None]
        return replies if replies else Response(status_code=202)
    reply = await handle_rpc(request, body)
    return Response(status_code=202) if reply is None else reply


@router.get("/mcp")
async def mcp_get() -> Response:
    # No server-initiated streams: tells clients to use POST only.
    return Response(status_code=405, headers={"Allow": "POST"})
