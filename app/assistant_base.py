"""What every assistant tool is built from: the core's (app/assistant.py, app/assistant_more.py) and each module's
(app/modules/<id>/tools.py). Part of the module SDK (docs/module-sdk.md)."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import text

from app.places import place_or_default

KM_PER_MILE = 1.609344


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    run: Callable[[Request, dict[str, Any]], Awaitable[dict[str, Any]]]


def _props(**fields: tuple[str, str]) -> dict[str, Any]:
    return {k: {"type": t, "description": d} for k, (t, d) in fields.items()}


def _need(args: dict[str, Any], key: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ToolError(f"Missing {key!r}. Try list_options to see what is set up.")
    return v.strip()


async def _wrap(coro: Awaitable[Any]) -> Any:
    try:
        return await coro
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc


def _miles(km: float) -> str:
    m = km / KM_PER_MILE
    return f"{m:.1f} mile{'s' if round(m, 1) != 1 else ''}"


def _ids(rows: Any) -> list[str]:
    return [r[0] for r in rows]


async def _point(request: Request, place: str | None) -> tuple[str, float, float]:
    """A saved place's name and position (the default place when none is given)."""
    pid = await _wrap(place_or_default(request, place or None))
    async with request.app.state.engine.connect() as conn:
        row = (await conn.execute(text("SELECT name, lat, lon FROM place WHERE id = :p"), {"p": pid})).first()
    if row is None:
        raise ToolError(f"No saved place {pid!r}. Try list_options.")
    return str(row[0]), float(row[1]), float(row[2])


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


PLACE = ("string", "id of a saved place; default: home")


async def _point_of(request: Request, args: dict[str, Any]) -> tuple[float, float]:
    """A saved place's or a postcode's point."""
    from app.geo import local_postcode

    if args.get("place"):
        from sqlalchemy import text as _text

        async with request.app.state.engine.connect() as c:
            want = str(args["place"]).lower()
            for r in await c.execute(_text("SELECT id, name, lat, lon FROM place")):
                if want in (str(r.id).lower(), str(r.name).lower()):
                    return float(r.lat), float(r.lon)
        raise ToolError(f"No saved place called {args['place']!r}")
    pc = str(args.get("postcode") or "").strip()
    if not pc:
        raise ToolError("Give lat and lon, a postcode, or a saved place")
    got = await local_postcode(request.app.state.engine, pc)
    if got is None:
        from app.geo import PostcodeError, lookup_postcode

        try:
            got = await lookup_postcode(pc)
        except PostcodeError as exc:
            raise ToolError(f"Could not find the postcode {pc!r} ({exc})") from exc
    return got.lat, got.lon
