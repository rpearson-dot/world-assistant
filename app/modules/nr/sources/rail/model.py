"""The departure record shared by every rail source (TfL, National Rail)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Departure:
    station: str | None
    line: str | None
    destination: str | None
    platform: str | None
    scheduled: datetime | None
    expected: datetime | None
    status: str | None  # e.g. OnTime, Delayed, Cancelled
    cause: str | None

    @property
    def when(self) -> datetime | None:
        return self.expected or self.scheduled

    @property
    def delay_minutes(self) -> int | None:
        if self.scheduled and self.expected:
            return round((self.expected - self.scheduled).total_seconds() / 60)
        return None

    def as_dict(self, now: datetime) -> dict[str, Any]:
        d = asdict(self)
        when = self.when
        d["scheduled"] = self.scheduled.isoformat() if self.scheduled else None
        d["expected"] = self.expected.isoformat() if self.expected else None
        d["delay_minutes"] = self.delay_minutes
        d["minutes"] = round((when - now).total_seconds() / 60) if when else None
        return d
