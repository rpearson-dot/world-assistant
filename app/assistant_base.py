"""What every assistant tool is built from (app/assistant.py and app/assistant_more.py both use it)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

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
