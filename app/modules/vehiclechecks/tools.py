"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.assistant_base import Tool, _props


# --- Tax and MOT -----------------------------------------------------------------------------------------------------
async def vehicle_checks_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from app.modules.vehiclechecks.vehiclecheck import check_all, list_checks, words

    if args.get("check"):
        await check_all(request.app.state.engine)
    d = await list_checks(request)
    vs = d["vehicles"]
    if args.get("vehicle"):
        want = str(args["vehicle"]).lower()
        vs = [v for v in vs if want in (v["id"], v["name"].lower(), str(v.get("registration") or "").lower())]
    if not vs:
        return {"text": "No vehicle has a registration yet: add one on the Vehicles page.", "data": d}
    return {"text": " ".join(words(v) for v in vs), "data": {"vehicles": vs}}


TOOLS: list[Tool] = [
    Tool("vehicle_checks", "Each saved vehicle's tax and MOT: when they are due, the last MOT's result, mileage and "
         "advisories, and any outstanding safety recall (from DVLA and DVSA, checked weekly).",
         {"type": "object", "properties": _props(vehicle=("string", "just this vehicle (name or registration)"),
                                                 check=("boolean", "ask DVLA and DVSA again now"))}, vehicle_checks_tool),
]
