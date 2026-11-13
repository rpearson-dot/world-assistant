"""The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

Declared on the module (provides "assistant.tools"); app/assistant.py gathers them."""

from __future__ import annotations

import re
from typing import Any

from fastapi import Request
from sqlalchemy import text

from app.assistant_base import (
    Tool,
    ToolError,
    _ids,
    _need,
    _props,
    _wrap,
)
from app.modules.bus.bus import live as bus_live
from app.modules.bus.bus import target as bus_target


async def _tracker(request: Request, args: dict[str, Any]) -> str:
    if args.get("tracker"):
        return str(args["tracker"])
    async with request.app.state.engine.connect() as conn:
        ids = _ids(await conn.execute(text("SELECT id FROM bus_tracker WHERE enabled ORDER BY id")))
    if len(ids) != 1:
        raise ToolError("Say which bus tracker." if ids else "No bus tracker is set up yet.")
    return ids[0]


async def bus(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    tid = await _tracker(request, args)
    tgt = await _wrap(bus_target(request, tid, None))
    parts = [tgt["summary"]]
    data: dict[str, Any] = {"target": tgt}
    try:
        lv = await _wrap(bus_live(request, tid))
        data["live"] = lv
        for key, rows in lv["next"].items():
            if rows and (not args.get("stop") or args["stop"] == key):
                parts.append(f"Next bus at {rows[0]['label']}: {rows[0]['minutes']} minutes.")
    except ToolError as exc:
        parts.append(f"Live times are unavailable ({exc}).")
    if tgt["pending"] and tgt["tracking_now"]:
        parts.append(f"{len(tgt['pending'])} scheduled bus(es) not yet seen.")
    return {"text": " ".join(parts), "data": data}


# --- Setting up a bus tracker --------------------------------------------------------------------------------------
_HM = re.compile(r"^([01]?\d|2[0-3]):?([0-5]\d)$")


def _hhmm(v: Any, what: str) -> str | None:
    if v in (None, ""):
        return None
    m = _HM.match(str(v).strip().replace(".", ":"))
    if not m:
        raise ToolError(f"{what} should be a time like 08:30, not {v!r}")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _stop_matches(query: str, stops: list[dict[str, Any]]) -> list[int]:
    """Positions of the stops a name means: an exact name first, else every stop containing all its words."""
    from app.modules.bus.sources.bus.setup import norm

    q = norm(query)
    exact = [i for i, s in enumerate(stops) if norm(s["name"]) == q or s["id"] == query.strip()]
    if exact:
        return exact
    words = q.split()
    return [i for i, s in enumerate(stops) if words and all(w in norm(s["name"]).split() or w in norm(s["name"]) for w in words)]


def _journey(dirs: dict[str, Any], board: str, target: str) -> tuple[str, dict[str, Any], dict[str, Any]] | str:
    """(direction, board stop, target stop) with the board stop before the target, or what went wrong."""
    seen: dict[str, list[str]] = {"board": [], "target": []}
    for d, v in dirs.items():
        stops = v["stops"]
        b, t = _stop_matches(board, stops), _stop_matches(target, stops)
        seen["board"] += [stops[i]["name"] for i in b]
        seen["target"] += [stops[i]["name"] for i in t]
        pairs = [(i, j) for i in b for j in t if i < j]
        if pairs:
            i, j = min(pairs, key=lambda p: p[1] - p[0])
            return d, stops[i], stops[j]
    for key, q in (("board", board), ("target", target)):
        if not seen[key]:
            return f"No stop on this line matches {q!r}."
    return f"{board!r} does not come before {target!r} in either direction."


def _stop_list(line: str, dirs: dict[str, Any]) -> str:
    return " ".join(f"Towards {v['towards']}: " + ", ".join(s["name"] for s in v["stops"]) + "." for v in dirs.values()) \
        or f"No stops found for the {line}."  # fmt: skip


async def bus_setup_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    from pydantic import ValidationError

    from app.modules.bus.bus import SetupIn, line_stops, setup
    from app.modules.bus.sources.bus import store

    line = _need(args, "line")
    got = await _wrap(line_stops(request, line, place=args.get("place") or None, lat=None, lon=None))
    dirs = {d: v for d, v in got.items() if not d.startswith("_")}
    board, target = str(args.get("board") or "").strip(), str(args.get("to") or "").strip()
    if not board or not target:
        return {"text": f"The {line}'s stops. {_stop_list(line, dirs)} Say where you get on and where you are going.",
                "data": got}  # fmt: skip
    found = _journey(dirs, board, target)
    if isinstance(found, str):
        return {"text": f"{found} {_stop_list(line, dirs)}", "data": got}
    direction, b, t = found
    deadline = _hhmm(args.get("arrive_by"), "arrive_by")
    if not deadline:
        raise ToolError("Say what time you need to arrive by (arrive_by, e.g. 08:30).")
    earliest = _hhmm(args.get("earliest"), "earliest")
    if not earliest:
        h, m = divmod(max(int(deadline[:2]) * 60 + int(deadline[3:]) - 60, 0), 60)
        earliest = f"{h:02d}:{m:02d}"
    back_e, back_d = _hhmm(args.get("return_earliest"), "return_earliest"), _hhmm(args.get("return_arrive_by"), "return_arrive_by")
    name = str(args.get("name") or re.sub(r"[^a-z0-9]+", "-", f"bus-{line}".lower()).strip("-"))
    async with request.app.state.engine.connect() as conn:
        existing = {r["id"] for r in await store.list_trackers(conn)}
    clash = [n for n in (name, f"{name}-return" if back_e else None) if n and n in existing]
    if clash and not args.get("replace"):
        raise ToolError(f"A tracker called {clash[0]!r} already exists. Give another name, or replace=true to replace it.")
    try:
        body = SetupIn(line=line, board=b["id"], target=t["id"], earliest=earliest, deadline=deadline, name=name,
                       return_earliest=back_e, return_deadline=back_d, source=got.get("_source"),
                       operator=got.get("_operator") if got.get("_source") == "bods" else None)  # fmt: skip
    except ValidationError as exc:
        raise ToolError("; ".join(e["msg"] for e in exc.errors())) from exc
    r = await _wrap(setup(request, body))
    text = (f"Tracking the {line} from {b['name']} to {t['name']} (towards {dirs[direction]['towards']}), "
            f"buses from {earliest}, to arrive by {deadline}, as tracker {r['id']!r}.")  # fmt: skip
    if r.get("return_tracker"):
        rt = r["return_tracker"]
        text += f" The way back, from {rt['board']} to {rt['target']}, {rt['earliest']} to arrive by {rt['deadline']}, is {rt['id']!r}."
    return {"text": text, "data": r}


TOOLS: list[Tool] = [
    Tool("bus", "Bus tracker: has a bus made it to the target stop before the deadline, and next buses at your stops.",
         {"type": "object", "properties": _props(tracker=("string", "tracker id; optional if only one"),
                                                 stop=("string", "only this stop key; optional"))}, bus),
    Tool("bus_setup", "Set up a bus tracker for a journey (acts: saves it). With just a line, lists its stops each way. "
         "With where you get on, where you are going and when you must arrive, it works out the direction and starts "
         "tracking: whether a bus gets you there in time, and the next buses. Stops can be named loosely, e.g. "
         "'Epsom station'. Will not replace an existing tracker unless replace is true.",
         {"type": "object", "properties": _props(
             line=("string", "the bus route, e.g. 406"), board=("string", "the stop you get on at"),
             to=("string", "the stop you are going to"), arrive_by=("string", "arrive by, e.g. 08:30"),
             earliest=("string", "the earliest bus to count; default an hour before arrive_by"),
             return_earliest=("string", "also track the way back: earliest bus"),
             return_arrive_by=("string", "the way back: arrive by"),
             name=("string", "tracker id, lower-case letters, digits, hyphens; default bus-<line>"),
             replace=("boolean", "replace a tracker of that name"),
             place=("string", "outside London: a saved place near the line, to find it; default home")),
          "required": ["line"]}, bus_setup_tool),
]
