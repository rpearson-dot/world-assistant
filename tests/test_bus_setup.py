from __future__ import annotations

from typing import Any, Self

import pytest
from fastapi.testclient import TestClient

from app import bus
from app.integrations.bus.config import TrackerConfig
from app.integrations.bus.setup import SetupError, build_config, describe
from app.main import app

SEQS = {
    "outbound": [{"id": "OUT00001", "name": "Depot"}, {"id": "OUT00002", "name": "Station Road"},
                 {"id": "OUT00003", "name": "Beta Lane"}, {"id": "OUT00004", "name": "Gamma Grove"},
                 {"id": "OUT00005", "name": "School Close"}, {"id": "OUT00006", "name": "Terminus"}],
    "inbound": [{"id": "INB00001", "name": "Terminus"}, {"id": "INB00002", "name": "School Close"},
                {"id": "INB00003", "name": "Beta Lane"}, {"id": "INB00004", "name": "Station Road"},
                {"id": "INB00005", "name": "Depot"}],
}  # fmt: skip


def test_describe_labels_each_direction_by_where_it_ends() -> None:
    d = describe(SEQS)
    assert d["outbound"]["towards"] == "Terminus" and d["inbound"]["towards"] == "Depot"
    assert d["outbound"]["stops"][2] == {"id": "OUT00003", "name": "Beta Lane", "pos": 2}


def test_everything_is_worked_out_from_two_choices_and_a_watched_stop() -> None:
    cfg = build_config("213", SEQS, "OUT00002", "OUT00005", ["OUT00003"], "07:00", "08:10")
    model = TrackerConfig.model_validate(cfg)  # the result is a valid tracker config
    t = model.target
    assert t and t.direction == "outbound"
    by_key = {s.key: s for s in model.stops}
    assert by_key[t.board].sid == "OUT00002" and by_key[t.alight].sid == "OUT00005"
    assert by_key[t.alight_return or ""].sid == "INB00002"  # same name, other side
    assert by_key[t.before_alight or ""].sid == "OUT00004"  # the stop before the target
    # the watched stop appears on both sides, sharing a place, so a turnaround there is noticed
    watched = [s for s in model.stops if s.place]
    assert {s.sid for s in watched} == {"OUT00003", "INB00003"} and len(
        {s.place for s in watched}
    ) == 1
    # legs timed: board -> watched -> target
    assert [(a, b) for a, b in model.segment_pairs()] == [
        ("OUT00002", "OUT00003"),
        ("OUT00003", "OUT00005"),
    ]


def test_no_watched_stops_and_no_return_side() -> None:
    seqs = {"outbound": SEQS["outbound"], "inbound": [{"id": "INB00009", "name": "Elsewhere"}]}
    cfg = build_config("213", seqs, "OUT00001", "OUT00002")
    assert cfg["target"]["alight_return"] is None and cfg["target"]["before_alight"] is None
    assert cfg["segments"] == [["board", "target"]]
    TrackerConfig.model_validate(cfg)


def test_bad_choices_are_explained() -> None:
    with pytest.raises(SetupError, match="opposite sides"):
        build_config("213", SEQS, "OUT00002", "INB00002")
    with pytest.raises(SetupError, match="comes before"):
        build_config("213", SEQS, "OUT00005", "OUT00002")
    with pytest.raises(SetupError, match="not on this line"):
        build_config("213", SEQS, "OUT00002", "NOPE0001")


class _FakeTfl:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def route_sequence(self, line: str, direction: str) -> list[dict[str, str]]:
        return SEQS[direction] if line == "213" else []


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("API_TOKEN", "tok")
    monkeypatch.setattr(bus, "_tfl", lambda _r: _FakeTfl())
    return TestClient(app)


def test_stops_endpoint(client: TestClient) -> None:
    h = {"Authorization": "Bearer tok"}
    d = client.get("/v1/bus/lines/213/stops", headers=h).json()
    assert d["outbound"]["towards"] == "Terminus" and len(d["inbound"]["stops"]) == 5
    assert client.get("/v1/bus/lines/999/stops", headers=h).status_code == 404
    assert client.get("/v1/bus/lines/bad%20line/stops", headers=h).status_code == 422


def test_setup_rejects_bad_choices_before_touching_the_database(client: TestClient) -> None:
    h = {"Authorization": "Bearer tok"}
    body: dict[str, Any] = {"line": "213", "board": "OUT00005", "target": "OUT00002"}
    r = client.post("/v1/bus/setup", json=body, headers=h)
    assert r.status_code == 422 and "comes before" in r.json()["detail"]


def test_the_way_back_must_be_fully_specified_and_workable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    h = {"Authorization": "Bearer tok"}
    body: dict[str, Any] = {"line": "213", "board": "OUT00002", "target": "OUT00005"}
    r = client.post("/v1/bus/setup", json={**body, "return_earliest": "15:00"}, headers=h)
    assert r.status_code == 422 and "both" in r.json()["detail"]  # nothing was stored: that was checked first
    # a way back whose far side does not exist is explained, not half-created
    odd = {"outbound": SEQS["outbound"], "inbound": [{"id": "INB00009", "name": "Elsewhere"}]}
    fake = type("T", (_FakeTfl,), {"route_sequence": lambda s, line, d: _async(odd[d])})()
    monkeypatch.setattr(bus, "_tfl", lambda _r: fake)
    r = client.post("/v1/bus/setup", json={**body, "return_earliest": "15:00", "return_deadline": "17:30"}, headers=h)
    assert r.status_code == 422 and "the way back" in r.json()["detail"]


async def _async(value: Any) -> Any:
    return value
