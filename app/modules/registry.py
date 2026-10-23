"""All the modules, and the questions the core asks of them.

Built-in modules come from `builtin.BUILTIN`. Outside modules (the way a third party adds one later) are found
two ways, both read once at start:

* Python packages that declare an entry point in the group ``worldassistant.modules``, naming a `Module`, or a
  list or function returning them;
* ``WA_EXTRA_MODULES``: comma-separated import strings (``package.module:NAME``) for a package you put on the
  Python path yourself.

An outside module that fails to load is logged and skipped; it can never stop the core starting. It cannot
replace a built-in (the first declaration of an id wins).
"""

from __future__ import annotations

import importlib
import logging
import os
import re
from collections.abc import Iterable
from functools import lru_cache
from importlib import metadata
from typing import Any

from app.modules.builtin import BUILTIN
from app.modules.model import Module

log = logging.getLogger("wa.modules")
GROUP = "worldassistant.modules"


def import_string(path: str) -> Any:
    """'package.module:attribute' -> the attribute."""
    module, _, attr = path.partition(":")
    if not module or not attr:
        raise ValueError(f"not an import string: {path!r}")
    return getattr(importlib.import_module(module), attr)


def _as_modules(obj: Any, origin: str) -> list[Module]:
    if callable(obj) and not isinstance(obj, Module):
        obj = obj()
    items: Iterable[Any] = [obj] if isinstance(obj, Module) else obj
    return [m for m in items if isinstance(m, Module)]


def _outside() -> list[Module]:
    found: list[Module] = []
    try:
        entry_points = metadata.entry_points(group=GROUP)
    except Exception:  # noqa: BLE001
        entry_points = []  # type: ignore[assignment]
    for ep in entry_points:
        try:
            dist = ep.dist.name if getattr(ep, "dist", None) else ep.name
            found += [_tagged(m, dist) for m in _as_modules(ep.load(), ep.name)]
        except Exception:
            log.warning("module %s could not be loaded", ep.name, exc_info=True)
    for path in filter(None, (p.strip() for p in os.environ.get("WA_EXTRA_MODULES", "").split(","))):
        try:
            found += [_tagged(m, path) for m in _as_modules(import_string(path), path)]
        except Exception:
            log.warning("module %s could not be loaded", path, exc_info=True)
    return found


def _tagged(m: Module, source: str) -> Module:
    from dataclasses import replace

    return replace(m, source=source, core=False)  # an outside module is never core: it can be switched off


@lru_cache(maxsize=1)
def modules() -> tuple[Module, ...]:
    out: dict[str, Module] = {}
    for m in (*BUILTIN, *_outside()):
        if m.id in out:
            log.warning("module id %r declared twice; keeping the first", m.id)
            continue
        out[m.id] = m
    return tuple(out.values())


def reload() -> None:
    """Forget what was discovered (tests, and after a package is added)."""
    modules.cache_clear()


def get(module_id: str) -> Module | None:
    return next((m for m in modules() if m.id == module_id), None)


def router_paths() -> list[tuple[str, str]]:
    """(module id, import string) for every router, in the order they are mounted."""
    return [(m.id, r) for m in modules() for r in m.routers]


def gated_prefixes() -> list[tuple[str, str]]:
    """(prefix, module id), longest first, for every module that can be switched off."""
    pairs = [(p, m.id) for m in modules() if not m.core for p in m.prefixes]
    return sorted(pairs, key=lambda x: (not x[0].startswith("re:"), -len(x[0])))


def path_matches(path: str, prefix: str) -> bool:
    """A prefix matches whole path segments; an entry written ``re:PATTERN`` must match the whole path."""
    if prefix.startswith("re:"):
        return re.fullmatch(prefix[3:], path) is not None
    return path == prefix or path.startswith(prefix + "/")


def module_for_path(path: str) -> str | None:
    for prefix, mid in gated_prefixes():
        if path_matches(path, prefix):
            return mid
    return None


def module_of_tool(tool: str) -> str | None:
    return next((m.id for m in modules() if tool in m.tools), None)


def worker_jobs() -> list[tuple[str, Any]]:
    """(module id, Job) for every worker job."""
    return [(m.id, j) for m in modules() for j in m.worker]
