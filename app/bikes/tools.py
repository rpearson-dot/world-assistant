"""The assistant's bike garage tools: the garage and what is due, logging what was done (by a part's label), a ride, and
tyre pressures. A rider can be named; with one rider, it is them."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy import text

from app.assistant_base import Tool, ToolError, _need, _props, _wrap
from app.bikes import api as A


async def _rider(request: Request, args: dict[str, Any]) -> str:
    async with request.app.state.engine.connect() as c:
        rows = [(r.id, r.name) for r in await c.execute(text("SELECT id, name FROM bike_rider ORDER BY created"))]
    if not rows:
        raise ToolError("No riders yet: add yourself on the Bikes page.")
    want = str(args.get("rider") or "").strip().lower()
    if want:
        hit = next((rid for rid, name in rows if want in (rid, name.lower())), None)
        if not hit:
            raise ToolError(f"No rider called {args['rider']!r} (riders: {', '.join(n for _, n in rows)}).")
        return hit
    if len(rows) > 1:
        raise ToolError(f"Which rider? ({', '.join(n for _, n in rows)})")
    return rows[0][0]


def _bike_label(g: dict[str, Any], bike_id: int | None) -> str:
    return next((b["label"] for b in g["bikes"] if b["id"] == bike_id), "")


def _due_text(g: dict[str, Any], only_due: bool = False) -> str:
    rows = [d for d in g["due"] if d["state"] == "due" or not only_due]
    if not rows:
        return "Nothing is due."
    bits = []
    for d in rows[:12]:
        where = _bike_label(g, d["bike_id"])
        left = next((f"{m['left']:g} {u} left" if m["left"] > 0 else f"{-m['left']:g} {u} over" for u, m in d["measures"].items()
                     if m["due"] or m["share"] >= 0.75), "")  # fmt: skip
        extra = f"; use the spare ({d['spare']['name']})" if d.get("spare") else ""
        bits.append(f"{d['name']}{' on ' + where if where else ''}: {d['state']}{', ' + left if left else ''}{extra}")
    return "; ".join(bits) + "."


async def bikes_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    rider = await _rider(request, args)
    g = await _wrap(A.garage(request, rider))
    bikes = ", ".join(f"{b['label']} ({b['km']:g} km logged)" for b in g["bikes"] if not b.get("retired_at")) or "no bikes yet"
    doubts = [r for r in g["ask_wet"] if r.get("doubt")]
    asks = (f" {len(g['ask_wet'])} rides to confirm (bike, and wet or dry) on the Bikes page"
            + (f"; {len(doubts)} look like the wrong bike ({doubts[-1]['doubt']})" if doubts else "") + "."
            if g["ask_wet"] else "")  # fmt: skip
    return {"text": f"Bikes: {bikes}. {_due_text(g)}{asks}", "data": g}


async def due_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    rider = await _rider(request, args)
    g = await _wrap(A.garage(request, rider))
    return {"text": _due_text(g, only_due=not args.get("coming")), "data": {"due": g["due"]}}


def _find_part(g: dict[str, Any], name: str, bike: str | None) -> dict[str, Any]:
    want = name.strip().lower()
    parts = [p for b in g["bikes"] if not bike or bike.lower() in b["label"].lower()
             for p in b["parts"] + [p for s in b["sets"] for p in s["parts"]]]  # fmt: skip
    parts += [p for p in g["kit"] if p not in parts]
    exact = [p for p in parts if (p.get("label") or "").lower() == want]
    hits = exact or [p for p in parts if want in p["name"].lower() or want in p["kind_name"].lower()]
    if not hits:
        raise ToolError(f"No part called {name!r}" + (f" on {bike}" if bike else "") + ".")
    if len({p["id"] for p in hits}) > 1:
        raise ToolError(f"Which one? {', '.join(sorted({p['name'] for p in hits}))}" + ("" if bike else " (and say which bike)") + ".")
    return hits[0]


async def service_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    rider = await _rider(request, args)
    action = _need(args, "action")
    g = await _wrap(A.garage(request, rider))
    part = _find_part(g, _need(args, "part"), args.get("bike"))
    spare = None
    if action == "replaced" and args.get("use_spare", True):
        spare = next((s for s in g["shelf"] if s["kind"] == part["kind"] and s["quantity"] > 0), None)
    body = A.ServiceIn(rider=rider, action=action, part_id=part["id"], cost=args.get("cost"), note=args.get("note"),
                       level_before=args.get("level_before"), spare_id=spare["id"] if spare else None,
                       new={k: args[k] for k in ("brand", "model", "price") if args.get(k)} or None)  # fmt: skip
    out = await _wrap(A.service(request, body))
    said = {"replaced": "Replaced", "serviced": "Serviced", "checked": "Checked", "charged": "Charged", "topped_up": "Topped up",
            "bled": "Bled", "measured_ok": "Measured and fine:", "cost": "Logged a cost for", "note": "Noted on",
            "crash": "Logged a crash for"}.get(action, action)  # fmt: skip
    extra = f" with the spare from the shelf ({out.get('spares_left', 0)} left)" if spare else ""
    return {"text": f"{said} {part['name']}{extra}. Its count starts again from now." if action in A.RESETS else f"{said} {part['name']}.",
            "data": out}


async def ride_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    rider = await _rider(request, args)
    g = await _wrap(A.garage(request, rider))
    bike = str(args.get("bike") or "").strip().lower()
    hits = [b for b in g["bikes"] if not b.get("retired_at") and (not bike or bike in b["label"].lower())]
    if len(hits) != 1:
        raise ToolError("Which bike? (" + ", ".join(b["label"] for b in g["bikes"]) + ")")
    when = args.get("when")
    started = datetime.fromisoformat(when) if when else datetime.now(UTC)
    body = A.RideIn(rider=rider, bike_id=hits[0]["id"], started=started, km=float(_need(args, "km")),
                    hours=float(args.get("hours") or 0), indoor=bool(args.get("indoor")),
                    wet=args.get("wet") if isinstance(args.get("wet"), bool) else None)  # fmt: skip
    out = await _wrap(A.add_ride(request, body))
    wet = "" if body.wet is not None or body.indoor else (
        f" Wet? Suggested {'yes' if out['wet_suggested'] else 'no' if out['wet_suggested'] is False else 'unknown'}"
        f" ({out['wet_why']}); say if not.")  # fmt: skip
    return {"text": f"Logged {body.km:g} km on {hits[0]['label']}.{wet}", "data": out}


async def pressure_tool(request: Request, args: dict[str, Any]) -> dict[str, Any]:
    rider = await _rider(request, args)
    g = await _wrap(A.garage(request, rider))
    bike = str(args.get("bike") or "").strip().lower()
    out = []
    for b in g["bikes"]:
        if b.get("retired_at") or (bike and bike not in b["label"].lower()):
            continue
        p = b["pressures"]
        if p:
            ends = "; ".join(f"{e} {v['psi']:g} psi ({v['bar']:g} bar){'' if v['own'] else ' suggested'}"
                             for e, v in sorted(p.items()))  # fmt: skip
            out.append(f"{b['label']}: {ends}")
    if not out:
        raise ToolError("No bike with tyres to give pressures for.")
    return {"text": ". ".join(out) + ".", "data": {b["label"]: b["pressures"] for b in g["bikes"]}}


ACTIONS = "replaced, serviced, checked, charged, topped_up, bled, measured_ok, cost, note or crash"
TOOLS: list[Tool] = [
    Tool("bikes", "The bike garage: each bike, what is due or coming up, and rides to confirm as wet or dry.",
         {"type": "object", "properties": _props(rider=("string", "the rider (needed when there is more than one)"))}, bikes_tool),
    Tool("bike_due", "What is due on the bikes (parts and services at 90% of their life or close to its end), with a spare "
         "from the shelf when there is one.",
         {"type": "object", "properties": _props(rider=("string", "the rider"), coming=("boolean", "include what is coming up"))},
         due_tool),
    Tool("bike_service", "Log what was done to a bike part (acts: records it, restarts the part's count; a replacement uses a "
         f"spare from the shelf when there is one). Actions: {ACTIONS}. Parts are named by their label (\"Chain B\", "
         "\"Battery A\") or kind (\"front pads\").",
         {"type": "object", "required": ["action", "part"],
          "properties": _props(action=("string", ACTIONS), part=("string", "the part's label or kind"),
                               bike=("string", "which bike, when the part is on more than one"), rider=("string", "the rider"),
                               level_before=("number", "a battery's charge (%) when it went on charge"),
                               cost=("number", "what it cost, £"), note=("string", "a note"),
                               brand=("string", "a new part's brand"), model=("string", "a new part's model"),
                               price=("number", "a new part's price, £"))}, service_tool),
    Tool("bike_ride", "Log a ride on a bike (acts: adds it; outdoors, a wet suggestion comes from the rain then).",
         {"type": "object", "required": ["km"],
          "properties": _props(km=("number", "distance in km"), bike=("string", "which bike"), hours=("number", "moving time, hours"),
                               when=("string", "start, ISO time (default now)"), indoor=("boolean", "on a trainer"),
                               wet=("boolean", "was it wet"), rider=("string", "the rider"))}, ride_tool),
    Tool("tyre_pressure", "Tyre pressures for each bike's fitted wheels: the starting ones from width, tubeless, the kind of "
         "riding and weight, or the ones set on the page.",
         {"type": "object", "properties": _props(bike=("string", "one bike"), rider=("string", "the rider"))}, pressure_tool),
]
