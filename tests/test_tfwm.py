"""TfWM: the GTFS-Realtime reader, tested on a feed built to the published protobuf layout."""

from __future__ import annotations

import pytest

from app.integrations.tfwm.client import DEFAULT_BASE, TfwmClient, TfwmError
from app.integrations.tfwm.gtfsrt import FeedError, parse_trip_updates


def varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def num(field: int, value: int) -> bytes:
    return varint(field << 3) + varint(value)


def raw(field: int, payload: bytes) -> bytes:
    return varint(field << 3 | 2) + varint(len(payload)) + payload


def text(field: int, s: str) -> bytes:
    return raw(field, s.encode())


def stop_update(stop: str, t: int, delay: int = 0) -> bytes:
    event = num(1, delay & 0xFFFFFFFFFFFFFFFF) + num(2, t)
    return raw(2, num(1, 3) + raw(2, event) + text(4, stop))


def entity(eid: str, route: str, vehicle: str, stops: bytes, deleted: bool = False) -> bytes:
    trip = text(1, "T" + eid) + text(3, "20261023") + text(5, route) + num(6, 1)
    update = raw(1, trip) + stops + raw(3, text(1, vehicle) + text(2, "label")) + num(4, 1_790_000_000)
    body = text(1, eid) + (num(2, 1) if deleted else b"") + raw(3, update) + num(99, 7)  # 99: a field it does not know
    return raw(2, body)


FEED = (raw(1, text(1, "2.0") + num(3, 1_790_000_100))
        + entity("a", "907", "E1205", stop_update("43000123", 1_790_000_300) + stop_update("43000124", 1_790_000_420, delay=-30))
        + entity("b", "11", "X1", stop_update("43000999", 1_790_000_500), deleted=True))


def test_a_feed_gives_trips_vehicles_and_predicted_times() -> None:
    stamp, updates = parse_trip_updates(FEED)
    assert stamp == 1_790_000_100
    assert len(updates) == 1  # the entity flagged deleted is left out
    u = updates[0]
    assert u["trip"]["route_id"] == "907" and u["trip"]["trip_id"] == "Ta" and u["trip"]["direction"] == 1
    assert u["vehicle"] == {"id": "E1205", "label": "label", "plate": ""}
    assert [s["stop_id"] for s in u["stops"]] == ["43000123", "43000124"]
    assert u["stops"][0]["arrival"]["time"] == 1_790_000_300
    assert u["stops"][1]["arrival"]["delay"] == -30  # negative delays survive the encoding


def test_an_empty_feed_is_empty_and_a_cut_off_one_is_refused() -> None:
    assert parse_trip_updates(b"") == (None, [])
    with pytest.raises(FeedError):
        parse_trip_updates(FEED[:-5])
    with pytest.raises(FeedError):
        parse_trip_updates(b"<html>Bad gateway</html>")


def test_the_client_needs_both_keys_and_uses_https_by_default() -> None:
    with pytest.raises(TfwmError, match="not both set"):
        TfwmClient("id", "")
    assert TfwmClient("id", "key").secure and DEFAULT_BASE.startswith("https://")
    assert not TfwmClient("id", "key", "http://api.tfwm.org.uk").secure  # only when asked for by name
