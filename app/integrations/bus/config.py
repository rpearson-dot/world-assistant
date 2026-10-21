"""Tracker configuration. Stops of interest are plain data supplied by the owner, so nothing
about a particular journey is built into the code or the repository."""

from __future__ import annotations

import re
from typing import Self

from pydantic import BaseModel, Field, model_validator

_KEY = r"^[a-z][a-z0-9_]{0,30}$"
_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
_SID = r"^[A-Za-z0-9]{4,30}$"


class StopOfInterest(BaseModel):
    key: str = Field(pattern=_KEY, description="short name used elsewhere in this config")
    sid: str = Field(pattern=_SID, description="TfL/NaPTAN stop id, e.g. 490001346C")
    label: str = Field(min_length=1, max_length=80)
    place: str | None = Field(
        None,
        pattern=_KEY,
        description="stops sharing a place name are the same place on opposite sides of the road; "
        "a bus seen at both in a row has turned round there",
    )


class Target(BaseModel):
    """The journey whose success is tracked: leave `board`, arrive at `alight` before the deadline."""

    board: str
    alight: str
    alight_return: str | None = Field(
        None, description="same place, opposite side: where a bus that terminates there drops off"
    )
    before_alight: str | None = Field(
        None,
        description="stop just before `alight`; confirms a bus that terminated really came from there",
    )
    direction: str = Field("outbound", pattern="^(outbound|inbound)$")


class TrackerConfig(BaseModel):
    line: str = Field(pattern=r"^[A-Za-z0-9-]{1,20}$")
    stops: list[StopOfInterest] = Field(min_length=1, max_length=30)
    target: Target | None = None
    segments: list[tuple[str, str]] | None = Field(
        None,
        description="pairs of stop keys to time; default: consecutive stops in the order listed",
    )
    sequences: dict[str, list[str]] | None = Field(
        None,
        description="optional override of the route's stop order: {'outbound': [sid, ...], 'inbound': [...]}",
    )
    earliest: str = Field("07:00", pattern=_HHMM)
    deadline: str = Field("08:10", pattern=_HHMM)
    min_travel_min: int = Field(12, ge=0, le=180)
    poll_start: str = Field("06:30", pattern=_HHMM)
    poll_end: str = Field("09:30", pattern=_HHMM)
    weekdays_only: bool = True

    @model_validator(mode="after")
    def _check(self) -> Self:
        keys = [s.key for s in self.stops]
        if len(set(keys)) != len(keys):
            raise ValueError("stop keys must be unique")
        known = set(keys)
        refs = list(self.target.model_dump(exclude={"direction"}).values()) if self.target else []
        refs += [k for pair in self.segments or [] for k in pair]
        missing = sorted({r for r in refs if r is not None and r not in known})
        if missing:
            raise ValueError(f"unknown stop keys: {', '.join(missing)}")
        for direction, sids in (self.sequences or {}).items():
            if direction not in ("outbound", "inbound") or not all(
                re.fullmatch(_SID, s) for s in sids
            ):
                raise ValueError("sequences must be {'outbound'|'inbound': [stop ids]}")
        return self

    def sid(self, key: str) -> str:
        return next(s.sid for s in self.stops if s.key == key)

    def place_of(self, sid: str) -> str | None:
        return next((s.place for s in self.stops if s.sid == sid), None)

    def segment_pairs(self) -> list[tuple[str, str]]:
        """Pairs of stop ids to time."""
        keys = self.segments or [
            (a.key, b.key) for a, b in zip(self.stops, self.stops[1:], strict=False)
        ]
        return [(self.sid(a), self.sid(b)) for a, b in keys]

    def target_cfg(self) -> dict[str, str | None] | None:
        """The shape events.target_hits expects, with stop ids rather than keys."""
        if not self.target:
            return None
        t = self.target
        return {
            "board": self.sid(t.board),
            "alight": self.sid(t.alight),
            "alight_return": self.sid(t.alight_return) if t.alight_return else None,
            "before_alight": self.sid(t.before_alight) if t.before_alight else None,
            "direction": t.direction,
        }
