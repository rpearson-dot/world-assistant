"""A small reader for Garmin's FIT activity files: enough to find a ride's GPS track, the devices it saw (with their
batteries) and any developer fields (tyre pressure sensors and the like write their readings that way).

FIT is a binary format of definition messages (which fields a local message type carries) and data messages. Nothing
here is specific to one device: each message comes back as its global message number and {field number: value}.
"""

from __future__ import annotations

import io
import struct
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

FIT_EPOCH = datetime(1989, 12, 31, tzinfo=UTC)
SEMICIRCLE = 180 / 2**31

# global message numbers worth naming
NAMES = {0: "file_id", 18: "session", 19: "lap", 20: "record", 21: "event", 23: "device_info", 34: "activity",
         206: "field_description", 207: "developer_data_id", 104: "device_aux_battery_info"}  # fmt: skip
SESSION, RECORD, DEVICE_INFO, FIELD_DESCRIPTION = 18, 20, 23, 206

# base type: (struct code, size, invalid value)
_TYPES: dict[int, tuple[str, int, Any]] = {
    0: ("B", 1, 0xFF), 1: ("b", 1, 0x7F), 2: ("B", 1, 0xFF), 3: ("h", 2, 0x7FFF), 4: ("H", 2, 0xFFFF),
    5: ("i", 4, 0x7FFFFFFF), 6: ("I", 4, 0xFFFFFFFF), 7: ("s", 1, None), 8: ("f", 4, None), 9: ("d", 8, None),
    10: ("B", 1, 0), 11: ("H", 2, 0), 12: ("I", 4, 0), 13: ("B", 1, None), 14: ("q", 8, 0x7FFFFFFFFFFFFFFF),
    15: ("Q", 8, 0xFFFFFFFFFFFFFFFF), 16: ("Q", 8, 0),
}  # fmt: skip


class FitError(ValueError):
    pass


@dataclass
class Message:
    num: int
    fields: dict[int, Any]
    dev: dict[tuple[int, int], Any] = field(default_factory=dict)  # (developer index, field number): raw bytes

    @property
    def name(self) -> str:
        return NAMES.get(self.num, str(self.num))


def _value(raw: bytes, base: int, little: bool) -> Any:
    code, size, invalid = _TYPES.get(base & 0x1F, ("B", 1, None))
    if code == "s":
        s = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
        return s or None
    if len(raw) % size:
        return None
    n = len(raw) // size
    vals = struct.unpack(("<" if little else ">") + code * n, raw)
    vals = tuple(None if (invalid is not None and v == invalid) else v for v in vals)
    if n == 1:
        return vals[0]
    return None if all(v is None for v in vals) else vals


def messages(data: bytes) -> Iterator[Message]:
    """Every data message in a FIT file (or several chained ones)."""
    pos = 0
    while pos + 12 <= len(data):
        hsize = data[pos]
        if hsize < 12 or data[pos + 8 : pos + 12] != b".FIT":
            if pos == 0:
                raise FitError("not a FIT file")
            return
        size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        p, end = pos + hsize, min(pos + hsize + size, len(data))
        defs: dict[int, tuple[int, bool, list[tuple[int, int, int]], list[tuple[int, int, int]]]] = {}
        while p < end:
            h = data[p]
            p += 1
            if h & 0x80:  # compressed timestamp header: a data message
                local, is_def, has_dev = (h >> 5) & 0x3, False, False
            else:
                local, is_def, has_dev = h & 0x0F, bool(h & 0x40), bool(h & 0x20)
            if is_def:
                little = data[p + 1] == 0
                num = struct.unpack("<H" if little else ">H", data[p + 2 : p + 4])[0]
                n = data[p + 4]
                p += 5
                fields = [(data[p + 3 * i], data[p + 3 * i + 1], data[p + 3 * i + 2]) for i in range(n)]
                p += 3 * n
                devs: list[tuple[int, int, int]] = []
                if has_dev:
                    nd = data[p]
                    p += 1
                    devs = [(data[p + 3 * i], data[p + 3 * i + 1], data[p + 3 * i + 2]) for i in range(nd)]
                    p += 3 * nd
                defs[local] = (num, little, fields, devs)
                continue
            if local not in defs:
                raise FitError(f"data before its definition at byte {p - 1}")
            num, little, fields, devs = defs[local]
            vals: dict[int, Any] = {}
            for fnum, fsize, base in fields:
                vals[fnum] = _value(data[p : p + fsize], base, little)
                p += fsize
            dv: dict[tuple[int, int], Any] = {}
            for fnum, fsize, idx in devs:
                dv[(idx, fnum)] = data[p : p + fsize]
                p += fsize
            yield Message(num, vals, dv)
        pos = end + 2  # each file ends with a two-byte CRC


def from_zip(blob: bytes) -> tuple[str, bytes]:
    """The activity file inside Garmin's download (a ZIP); or the bytes themselves if they are not one."""
    if not blob.startswith(b"PK"):
        return "activity", blob
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = sorted(z.namelist(), key=lambda n: (not n.lower().endswith(".fit"), n))
        if not names:
            raise FitError("the download is an empty ZIP")
        return names[0], z.read(names[0])


def when(ts: int | None) -> datetime | None:
    return FIT_EPOCH + timedelta(seconds=ts) if ts is not None else None


@dataclass
class Summary:
    counts: dict[str, int]
    track: list[tuple[datetime | None, float, float]]  # (time, lat, lon)
    devices: list[dict[str, Any]]
    dev_fields: list[dict[str, Any]]
    sport: tuple[int | None, int | None] | None


def summarise(data: bytes) -> Summary:
    counts: dict[str, int] = {}
    track: list[tuple[datetime | None, float, float]] = []
    devices: list[dict[str, Any]] = []
    dev_fields: list[dict[str, Any]] = []
    dev_seen: dict[tuple[int, int], int] = {}
    sport = None
    for m in messages(data):
        counts[m.name] = counts.get(m.name, 0) + 1
        if m.num == RECORD:
            la, lo = m.fields.get(0), m.fields.get(1)
            if isinstance(la, int) and isinstance(lo, int):
                track.append((when(m.fields.get(253)), la * SEMICIRCLE, lo * SEMICIRCLE))
        elif m.num == DEVICE_INFO:
            f = m.fields
            devices.append({"index": f.get(0), "type": f.get(1), "manufacturer": f.get(2), "product": f.get(4),
                            "name": f.get(27), "battery_v": round(f[10] / 256, 2) if isinstance(f.get(10), int) else None,
                            "battery_status": f.get(11), "battery_level": f.get(32)})  # fmt: skip
        elif m.num == FIELD_DESCRIPTION:
            dev_fields.append({"developer": m.fields.get(0), "field": m.fields.get(1), "name": m.fields.get(3),
                               "units": m.fields.get(8)})  # fmt: skip
        elif m.num == SESSION and sport is None:
            sport = (m.fields.get(5), m.fields.get(6))
        for k in m.dev:
            dev_seen[k] = dev_seen.get(k, 0) + 1
    for d in dev_fields:
        d["readings"] = dev_seen.get((d["developer"], d["field"]), 0)
    return Summary(counts, track, devices, dev_fields, sport)
