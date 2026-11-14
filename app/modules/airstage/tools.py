"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask)."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, ToolError, _props, _wrap


async def aircon(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.airstage.api import overview

    o = await _wrap(overview(request))
    parts = []
    for u in o["units"]:
        s = u.get("state")
        if not s:
            parts.append(f"{u['name']}: not answering.")
            continue
        what = f"{s['mode'] or 'on'} to {s['target_c']}°" if s["power"] else "off"
        parts.append(f"{u['name']}: {what}, {s['indoor_c']}° inside.")
    return {"text": " ".join(parts) or "No air conditioners are set up.", "data": o}


async def aircon_set(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.airstage.api import change
    from app.modules.airstage.unit import UnitError

    want = {k: args[k] for k in ("power", "mode", "target_c", "fan") if args.get(k) is not None}
    try:
        r = await change(request.app.state.engine, str(args.get("unit") or ""), want, "assistant")
    except (LookupError, ValueError, UnitError) as exc:
        raise ToolError(str(exc)) from exc
    s = r["state"]
    return {"text": f"Done: {r['unit']} is {'off' if not s['power'] else (s['mode'] or 'on') + ' at ' + str(s['target_c']) + '°'}.", "data": r}


TOOLS: list[Tool] = [
    Tool("aircon", "The air conditioners: on or off, mode, target and the temperature inside and outside.",
         {"type": "object", "properties": _props()}, aircon),
    Tool("aircon_set", "Change an air conditioner: power on or off, mode (auto, heat, cool, dry, fan_only), target "
         "temperature in °C (half degrees), fan (auto, quiet, low, medium, high). 'unit' is its id from aircon.",
         {"type": "object", "properties": _props(unit=("string", "the unit's id"), power=("boolean", "on or off"),
                                                  mode=("string", "auto, heat, cool, dry or fan_only"),
                                                  target_c=("number", "target temperature, °C"),
                                                  fan=("string", "auto, quiet, low, medium or high")), "required": ["unit"]},
         aircon_set),
]
