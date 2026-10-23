"""What a module is: one declaration that says everything the core needs to know about a feature.

A module is a feature with an outside source or a job of its own: fuel prices, the bus tracker, flood warnings.
It declares, as data, what it needs (keys, other modules, optional containers), what it adds (routes, scheduled
jobs, assistant tools) and how to describe it on the Setup page. The core reads these declarations and wires
everything; a module never edits the core.

Everything that runs code is named by an import string ("app.floods:router") and imported only when needed, so
a module that is switched off, or one that comes from outside the image, costs nothing until it is used.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# What a cron entry is: arq `cron()` keyword arguments, or a function of the settings that returns them (for
# intervals the user can change).
CronSpec = dict[str, Any] | Callable[[Any], dict[str, Any]]


@dataclass(frozen=True)
class Job:
    """A worker job: where its function is, and when it runs (no cron = started by something else)."""

    func: str  # "app.integrations.flood.sync:flood_poll"
    cron: CronSpec | None = None


@dataclass(frozen=True)
class Module:
    id: str
    name: str
    unlocks: str  # one line: what it gives you
    category: str = "data"  # data | transport | energy | home | place | service
    core: bool = False  # always on, cannot be switched off
    # keys
    keys: tuple[str, ...] = ()  # secret names; empty = needs no key
    any_key: bool = False  # one of the keys is enough
    key_optional: bool = False  # works without, better with
    extra_ready: tuple[str, ...] = ()  # settings attributes that count as "set up" too
    # status
    jobs: tuple[str, ...] = ()  # job_run names; empty = asked live, no stored runs
    switch: str = ""  # settings attribute that turns the poll on
    link: str = ""
    note: str = ""
    listed: bool = True  # shown on the Setup list as a data source
    # what it adds
    routers: tuple[str, ...] = ()  # import strings of APIRouters
    prefixes: tuple[str, ...] = ()  # URL path prefixes that go dark when it is switched off
    worker: tuple[Job, ...] = ()
    tools: tuple[str, ...] = ()  # assistant (MCP) tool names
    # how it relates to the rest
    needs: tuple[str, ...] = ()  # module ids it cannot work without
    services: tuple[str, ...] = ()  # optional containers: compose file names
    coverage: tuple[str, ...] = ()  # region words ("london"); empty = everywhere
    area: tuple[float, float, float, float] | None = None  # (min lat, min lon, max lat, max lon) of its coverage
    source: str = "builtin"  # builtin | the distribution name of an outside package
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    def covers(self, lat: float, lon: float, regions: list[str]) -> bool:
        """Is a place inside what this module covers? Everywhere-modules cover everything."""
        if not self.coverage and self.area is None:
            return True
        if self.area is not None:
            s, w, n, e = self.area
            if s <= lat <= n and w <= lon <= e:
                return True
        names = " ".join(regions).lower()
        return any(word in names for word in self.coverage)
