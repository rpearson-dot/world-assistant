"""Read the parts of a GTFS-Realtime TripUpdates feed that a bus countdown needs.

GTFS-Realtime is protocol buffers. Rather than add a dependency for four message types, this reads the wire
format directly (varints and length-delimited fields) and picks out only what is used. Anything it does not
know is skipped, so new fields in the feed do not break it. Field numbers are from the published
gtfs-realtime.proto:

    FeedMessage:     header = 1, entity = 2 (repeated)
    FeedEntity:      id = 1, is_deleted = 2, trip_update = 3
    TripUpdate:      trip = 1, stop_time_update = 2 (repeated), vehicle = 3, timestamp = 4, delay = 5
    TripDescriptor:  trip_id = 1, start_time = 2, start_date = 3, schedule_relationship = 4, route_id = 5, direction_id = 6
    StopTimeUpdate:  stop_sequence = 1, arrival = 2, departure = 3, stop_id = 4, schedule_relationship = 5
    StopTimeEvent:   delay = 1, time = 2, uncertainty = 3
    VehicleDescriptor: id = 1, label = 2, license_plate = 3
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

MAX_DEPTH = 6


class FeedError(ValueError):
    """The bytes are not a feed this reader can follow."""


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = value = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise FeedError("the feed ended in the middle of a number")
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, i
        shift += 7


def _fields(buf: bytes) -> Iterator[tuple[int, int, Any]]:
    """(field number, wire type, value) for each field; the value is an int, or bytes for length-delimited."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 2:
            n, i = _varint(buf, i)
            if i + n > len(buf):
                raise FeedError("a field ran past the end of the feed")
            value, i = buf[i : i + n], i + n
        elif wire == 1:
            value, i = buf[i : i + 8], i + 8
        elif wire == 5:
            value, i = buf[i : i + 4], i + 4
        else:
            raise FeedError(f"unknown field type {wire}")
        yield number, wire, value


def _signed(v: int, bits: int = 64) -> int:
    return v - (1 << bits) if v >= 1 << (bits - 1) else v


def _text(b: Any) -> str:
    return b.decode("utf-8", "replace") if isinstance(b, bytes) else str(b)


def _event(buf: bytes) -> dict[str, int | None]:
    out: dict[str, int | None] = {"delay": None, "time": None}
    for n, w, v in _fields(buf):
        if w == 0 and n == 1:
            out["delay"] = _signed(v, 32) if v < 1 << 32 else _signed(v)
        elif w == 0 and n == 2:
            out["time"] = _signed(v)
    return out


def _stop_update(buf: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {"stop_id": "", "sequence": None, "arrival": None, "departure": None, "skipped": False}
    for n, w, v in _fields(buf):
        if n == 1 and w == 0:
            out["sequence"] = v
        elif n == 2 and w == 2:
            out["arrival"] = _event(v)
        elif n == 3 and w == 2:
            out["departure"] = _event(v)
        elif n == 4 and w == 2:
            out["stop_id"] = _text(v)
        elif n == 5 and w == 0:
            out["skipped"] = v == 1  # SKIPPED
    return out


def _trip(buf: bytes) -> dict[str, Any]:
    out = {"trip_id": "", "route_id": "", "direction": None, "start_date": "", "start_time": ""}
    for n, w, v in _fields(buf):
        if n == 1 and w == 2:
            out["trip_id"] = _text(v)
        elif n == 2 and w == 2:
            out["start_time"] = _text(v)
        elif n == 3 and w == 2:
            out["start_date"] = _text(v)
        elif n == 5 and w == 2:
            out["route_id"] = _text(v)
        elif n == 6 and w == 0:
            out["direction"] = v
    return out


def _vehicle(buf: bytes) -> dict[str, str]:
    out = {"id": "", "label": "", "plate": ""}
    for n, w, v in _fields(buf):
        if w == 2 and n in (1, 2, 3):
            out[("id", "label", "plate")[n - 1]] = _text(v)
    return out


def _trip_update(buf: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {"trip": {}, "vehicle": {}, "stops": [], "timestamp": None, "delay": None}
    for n, w, v in _fields(buf):
        if n == 1 and w == 2:
            out["trip"] = _trip(v)
        elif n == 2 and w == 2:
            out["stops"].append(_stop_update(v))
        elif n == 3 and w == 2:
            out["vehicle"] = _vehicle(v)
        elif n == 4 and w == 0:
            out["timestamp"] = v
        elif n == 5 and w == 0:
            out["delay"] = _signed(v, 32) if v < 1 << 32 else _signed(v)
    return out


def parse_trip_updates(body: bytes) -> tuple[int | None, list[dict[str, Any]]]:
    """(feed timestamp, trip updates) from a GTFS-Realtime FeedMessage. Entities flagged deleted are left out."""
    stamp: int | None = None
    updates: list[dict[str, Any]] = []
    for n, w, v in _fields(body):
        if n == 1 and w == 2:  # header: timestamp = 3
            for hn, hw, hv in _fields(v):
                if hn == 3 and hw == 0:
                    stamp = hv
        elif n == 2 and w == 2:
            deleted, update = False, None
            for en, ew, ev in _fields(v):
                if en == 2 and ew == 0:
                    deleted = ev == 1
                elif en == 3 and ew == 2:
                    update = _trip_update(ev)
            if update and not deleted:
                updates.append(update)
    return stamp, updates
