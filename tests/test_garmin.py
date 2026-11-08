"""Garmin Connect's Personal API: rides from activities, the sync (CSV twins, rules, deletions, weight), the key, and
the FIT reader."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.bikes import fit
from app.bikes import garmin as G
from tests.test_bikes import DB, garage  # noqa: F401 - the fixture

NOW = datetime.now(UTC).replace(microsecond=0)


def act(aid: int, kind: str, days_ago: float, km: float, **kw: Any) -> dict[str, Any]:
    start = NOW - timedelta(days=days_ago)
    return {"activityId": aid, "activityName": f"Ride {aid}", "activityType": {"typeKey": kind},
            "beginTimestamp": int(start.timestamp() * 1000), "distance": km * 1000, "movingDuration": 3600.0,
            "startLatitude": 51.37, "startLongitude": -0.25, **kw}  # fmt: skip


def test_activities_become_rides_and_only_rides() -> None:
    r = G.to_ride(act(7, "gravel_cycling", 1, 42.5), "sam")
    assert r and r["id"] == "garmin:7" and r["km"] == 42.5 and r["hours"] == 1.0 and not r["indoor"]
    assert r["activity"] == "Gravel Cycling" and r["source"] == "garmin"
    assert G.to_ride(act(8, "indoor_cycling", 1, 20), "sam")["indoor"]  # type: ignore[index]
    assert G.to_ride(act(9, "virtual_ride", 1, 20), "sam")["indoor"]  # type: ignore[index]
    for k in ("running", "motorcycling", "walking", "strength_training"):
        assert G.to_ride(act(10, k, 1, 5), "sam") is None, k
    assert G.to_ride(act(11, "e_bike_mountain", 1, 5), "sam")["activity"] == "E Bike Mountain"  # type: ignore[index]
    # Garmin's commute: as the event type (Transportation) or the ride's own type
    c = G.to_ride(act(12, "road_biking", 1, 9, eventType={"typeKey": "transportation"}), "sam")
    assert c and c["commute"] and c["activity"] == "Road Biking (Commute)"
    assert G.to_ride(act(13, "bike_commute", 1, 9), "sam")["commute"]  # type: ignore[index]
    rules = {"commute": 5, "road_biking": 7, "default": 9}
    assert (G.rule_for(rules, "road_biking", True), G.rule_for(rules, "road_biking", False), G.rule_for(rules, "x", False)) == (5, 7, 9)


def test_the_latest_weight_in_kg_and_twins_by_time_and_distance() -> None:
    got = {"dailyWeightSummaries": [{"summaryDate": "2026-10-01", "latestWeight": {"weight": 83000.0}},
                                    {"summaryDate": "2026-11-01", "latestWeight": {"weight": 81234.0}}]}  # fmt: skip
    assert G.latest_weight(got) == 81.2 and G.latest_weight({}) is None
    a = {"started": NOW, "km": 40.0}
    assert G.same_ride(a, {"started": NOW + timedelta(minutes=4), "km": 40.6})
    assert not G.same_ride(a, {"started": NOW + timedelta(minutes=9), "km": 40.0})
    assert not G.same_ride(a, {"started": NOW, "km": 43.0})


def _token(hours: float) -> str:
    def b(d: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{b({'alg': 'none'})}.{b({'exp': time.time() + hours * 3600})}.sig"


class FakeGarmin:
    def __init__(self, acts: list[dict[str, Any]], key: str = "good-key-123", token_hours: float = 24) -> None:
        self.acts, self.key, self.token_hours, self.calls = acts, key, token_hours, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(f"{req.method} {req.url.path}")
        if req.url.path == G.TOKEN_PATH:
            if req.headers.get("Authorization") != f"ApiKey {self.key}":
                return httpx.Response(401, text="unauthorized")
            return httpx.Response(200, text=_token(self.token_hours))
        assert req.headers["Authorization"].startswith("Bearer ")
        if req.url.path == G.SEARCH:
            return httpx.Response(200, json=self.acts if req.url.params["start"] == "0" else [])
        if req.url.path.startswith("/weight-service/"):
            return httpx.Response(200, json={"dailyWeightSummaries": [{"summaryDate": "2026-11-01", "latestWeight": {"weight": 81234.0}}]})
        return httpx.Response(404)


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_the_sync_brings_rides_in_keeps_what_was_said_about_imported_ones_and_follows_garmin(
        garage: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811  # fmt: skip
    from app.main import app

    monkeypatch.setenv("WA_KEYS_DIR", str(tmp_path))
    G._tokens.clear()
    c = garage
    assert c.post("/v1/bikes/riders", json={"name": "Sam Test", "weight_kg": 70}).status_code == 201
    bike = c.post("/v1/bikes/bikes", json={"rider": "sam-test", "label": "Gravel", "type": "gravel"}).json()["id"]
    # a ride imported from the CSV before the key, said to be wet: the same ride as Garmin's activity 1
    when = (NOW - timedelta(days=10)).astimezone(G_TZ()).strftime("%Y-%m-%d %H:%M:%S")
    csv = f"Activity Type,Date,Title,Distance,Moving Time\nGravel Cycling,{when},Ride 1,40.0,01:00:00\n"
    assert c.post("/v1/bikes/rides/import", json={"rider": "sam-test", "csv": csv, "bike_id": bike}).json()["added"] == 1
    csv_id = c.get("/v1/bikes/rides?rider=sam-test").json()["rides"][0]["id"]
    c.patch(f"/v1/bikes/rides/{csv_id}", json={"wet": True})

    # no key yet: nothing to sync
    assert c.post("/v1/bikes/garmin/sync?rider=sam-test").json()["skipped"]
    (tmp_path / "garmin_sam_test.txt").write_text("good-key-123\n")
    assert G.riders_with_keys() == ["sam-test"]
    r = c.put("/v1/bikes/garmin/rules?rider=sam-test", json={"rules": {"gravel_cycling": bike}})
    assert r.status_code == 200 and r.json()["rules"] == {"gravel_cycling": bike}
    assert c.put("/v1/bikes/garmin/rules?rider=sam-test", json={"rules": {"x": 99999}}).status_code == 422

    fake = FakeGarmin([act(1, "gravel_cycling", 10, 40.1), act(2, "gravel_cycling", 2, 55), act(3, "indoor_cycling", 3, 25),
                       act(4, "running", 1, 10)], token_hours=5)  # fmt: skip

    def run() -> dict[str, Any]:
        async def go() -> dict[str, Any]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as client:
                return await G.sync(app.state.engine, "sam-test", client)

        return asyncio.run(go())

    out = run()
    assert out["full"] and out["activities"] == 4 and out["rides"] == 3
    assert (out["added"], out["from_csv"], out["removed"], out["weight_kg"]) == (2, 1, 0, 81.2)
    rides = {x["id"]: x for x in c.get("/v1/bikes/rides?rider=sam-test").json()["rides"]}
    assert set(rides) == {"garmin:1", "garmin:2", "garmin:3"}  # the CSV ride became Garmin's
    assert rides["garmin:1"]["wet"] is True and rides["garmin:1"]["bike_id"] == bike  # what was said about it kept
    assert rides["garmin:2"]["bike_id"] == bike and not rides["garmin:2"]["bike_checked"]  # by the rule; still asked
    assert rides["garmin:2"]["wet_suggested"] is True  # from the rain gauge (faked in the fixture)
    assert rides["garmin:3"]["indoor"] and rides["garmin:3"]["bike_id"] is None
    s = c.get("/v1/bikes/garmin?rider=sam-test").json()
    assert s["key_set"] and s["last_result"]["added"] == 2 and "gravel_cycling" in s["types"]
    assert s["key_expires"] and s["warn"]  # the token stopped early, so the key runs out today: warned
    assert c.get("/v1/bikes/riders").json()["riders"][-1]["weight_kg"] == 81.2

    # in Garmin: ride 2 deleted, ride 3's distance corrected; the next sync looks back a month and follows
    fake.acts = [act(1, "gravel_cycling", 10, 40.1), act(3, "indoor_cycling", 3, 27.5)]
    out = run()
    assert not out["full"] and (out["added"], out["updated"], out["removed"]) == (0, 1, 1)
    rides = {x["id"]: x for x in c.get("/v1/bikes/rides?rider=sam-test").json()["rides"]}
    assert set(rides) == {"garmin:1", "garmin:3"} and rides["garmin:3"]["km"] == 27.5

    # a refused key: the sync says so on the page
    fake.key = "another"
    G._tokens.clear()
    with pytest.raises(G.GarminError, match="refused the key"):
        run()
    assert "refused" in c.get("/v1/bikes/garmin?rider=sam-test").json()["error"]

    # saving a key: checked with Garmin first, never kept if refused, then forgotten
    real = httpx.AsyncClient
    monkeypatch.setattr(G.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(fake), **kw))
    r = c.put("/v1/bikes/garmin/key?rider=sam-test", json={"value": "wrong-key-xyz"})
    assert r.status_code == 422 and "refused" in r.json()["detail"]
    r = c.put("/v1/bikes/garmin/key?rider=sam-test", json={"value": "another", "expires": "2027-11-08"})
    assert r.status_code == 422  # shorter than a key
    fake.key = "another-key-1"
    r = c.put("/v1/bikes/garmin/key?rider=sam-test", json={"value": "another-key-1", "expires": "2027-11-08"})
    assert r.status_code == 200 and r.json()["checked"]
    assert (tmp_path / "garmin_sam_test.txt").read_text() == "another-key-1"
    assert c.get("/v1/bikes/garmin?rider=sam-test").json()["key_expires"] == "2027-11-08"
    assert c.delete("/v1/bikes/garmin/key?rider=sam-test").json()["key_set"] is False


def G_TZ() -> Any:
    from app.bikes.api import TZ

    return TZ


def _fit(messages: list[bytes]) -> bytes:
    data = b"".join(messages)
    return struct.pack("<BBHI4s", 12, 0x20, 2100, len(data), b".FIT") + data + b"\0\0"


def test_the_fit_reader_finds_the_track_devices_and_developer_fields() -> None:
    # record (20): timestamp uint32, lat sint32, lon sint32
    d_rec = bytes([0x40, 0, 0]) + struct.pack("<H", 20) + bytes([3, 253, 4, 0x86, 0, 4, 0x85, 1, 4, 0x85])
    lat, lon = int(51.37 / fit.SEMICIRCLE), int(-0.25 / fit.SEMICIRCLE)
    rec = bytes([0x00]) + struct.pack("<Iii", 1_100_000_000, lat, lon)
    no_fix = bytes([0x00]) + struct.pack("<Iii", 1_100_000_001, 0x7FFFFFFF, 0x7FFFFFFF)
    # device_info (23) as local type 1: device index uint8, battery voltage uint16, battery status enum
    d_dev = bytes([0x41, 0, 0]) + struct.pack("<H", 23) + bytes([3, 0, 1, 0x02, 10, 2, 0x84, 11, 1, 0x00])
    dev = bytes([0x01, 2]) + struct.pack("<H", 3 * 256 + 128) + bytes([3])
    # field_description (206) as local type 2: developer index, field number, name
    d_desc = bytes([0x42, 0, 0]) + struct.pack("<H", 206) + bytes([3, 0, 1, 0x02, 1, 1, 0x02, 3, 16, 0x07])
    desc = bytes([0x02, 0, 5]) + b"tire_pressure".ljust(16, b"\0")
    s = fit.summarise(_fit([d_rec, rec, no_fix, d_dev, dev, d_desc, desc]))
    assert s.counts == {"record": 2, "device_info": 1, "field_description": 1}
    assert len(s.track) == 1 and abs(s.track[0][1] - 51.37) < 1e-6 and s.track[0][0] == fit.when(1_100_000_000)
    assert s.devices[0]["battery_v"] == 3.5 and s.devices[0]["battery_status"] == 3
    assert s.dev_fields[0]["name"] == "tire_pressure"
    with pytest.raises(fit.FitError):
        list(fit.messages(b"not a fit file at all"))
    assert fit.from_zip(b"raw") == ("activity", b"raw")
