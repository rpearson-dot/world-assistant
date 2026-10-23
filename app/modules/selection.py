"""Which modules the user has switched on: a small saved choice, read with a short cache.

No saved choice (an install from before modules existed, or a user who never chose) means every module is on,
so an upgrade changes nothing. Two shapes once chosen:

    {"mode": "all",  "off": ["fuel"]}      everything except these
    {"mode": "only", "on":  ["fuel", "bus"]}   only these (and what they need), plus the always-on core

A database that cannot answer leaves everything on: the choice must never be the reason the service is down.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from app.modules.model import Module

KEY = "modules"
CACHE_SECONDS = 15.0


@dataclass(frozen=True)
class Selection:
    mode: str = "all"  # all | only
    off: frozenset[str] = field(default_factory=frozenset)
    on: frozenset[str] = field(default_factory=frozenset)
    chosen: bool = False  # false = nobody has chosen yet

    def to_json(self) -> dict[str, Any]:
        return {"mode": self.mode, "off": sorted(self.off), "on": sorted(self.on)}

    @staticmethod
    def from_json(raw: Any) -> Selection:
        if not isinstance(raw, dict) or raw.get("mode") not in ("all", "only"):
            return Selection()
        ids = lambda k: frozenset(str(x) for x in raw.get(k) or [] if isinstance(x, str))
        return Selection(mode=raw["mode"], off=ids("off"), on=ids("on"), chosen=True)


def enabled_ids(modules: list[Module], sel: Selection) -> set[str]:
    """The ids that are on. Core modules always are; what an enabled module needs is switched on with it."""
    by_id = {m.id: m for m in modules}
    if sel.mode == "only" and sel.chosen:
        wanted = set(sel.on)
    else:
        wanted = {m.id for m in modules if m.id not in sel.off}
    wanted |= {m.id for m in modules if m.core}
    stack = list(wanted)
    while stack:  # dependencies come along
        for need in by_id.get(stack.pop(), Module("", "", "")).needs:
            if need in by_id and need not in wanted:
                wanted.add(need)
                stack.append(need)
    return wanted


_cache: dict[str, Any] = {"at": -1e9, "sel": Selection()}


def clear_cache() -> None:
    _cache.update(at=-1e9, sel=Selection())


async def load(engine: Any, *, fresh: bool = False) -> Selection:
    now = time.monotonic()
    if not fresh and now - _cache["at"] < CACHE_SECONDS:
        return _cache["sel"]
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": KEY})).first()
        sel = Selection.from_json(row[0] if row else None)
    except Exception:  # noqa: BLE001 - no table yet, database down: leave everything on
        return _cache["sel"] if _cache["at"] > 0 else Selection()
    _cache.update(at=now, sel=sel)
    return sel


async def save(engine: Any, sel: Selection) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
                 "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
            {"k": KEY, "v": json.dumps(sel.to_json())},
        )  # fmt: skip
    clear_cache()


async def reset(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM app_setting WHERE key = :k"), {"k": KEY})
    clear_cache()
