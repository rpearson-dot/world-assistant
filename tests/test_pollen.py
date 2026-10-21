from datetime import date
from typing import Any, Self

import httpx
import pytest

from app import assistant
from app.integrations.pollen.client import PollenClient, PollenError, parse_forecast, risk_level

RAW = {
    "contamination": [
        {"poll_id": 1, "poll_title": "Birch", "contamination_1": 0, "contamination_2": 1,
         "contamination_3": 2, "contamination_4": 9},
        {"poll_id": 2, "poll_title": "Grasses", "contamination_1": 3, "contamination_2": "2",
         "contamination_3": None, "contamination_4": 4},
    ],
    "allergyrisk": {"allergyrisk_1": 6, "allergyrisk_2": 2.4, "allergyrisk_3": 0, "allergyrisk_4": 10},
    "allergyrisk_hourly": {"allergyrisk_hourly_1": list(range(24)), "allergyrisk_hourly_2": "bad"},
}  # fmt: skip
TODAY = date(2026, 10, 21)


def client(handler: Any) -> PollenClient:
    async def nosleep(_: float) -> None:
        return None

    return PollenClient("KEY123", transport=httpx.MockTransport(handler), sleep=nosleep)


def test_parse_clamps_levels_and_labels_them() -> None:
    f = parse_forecast(RAW, TODAY)
    assert f["dates"] == ["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24"]
    birch, grass = f["allergens"]
    assert birch["levels"] == [0, 1, 2, 4] and birch["labels"][0] == "none"
    assert grass["levels"] == [3, 2, None, 4] and grass["labels"][2] is None
    assert f["allergy_risk_level"] == [2, 1, 0, 4] and f["allergy_risk_label"][3] == "very high"
    assert f["allergy_risk_hourly"][0][:3] == [0, 1, 2] and f["allergy_risk_hourly"][1] is None


def test_risk_scale_matches_the_allergen_scale() -> None:
    assert [risk_level(v) for v in (0, 1, 3, 5, 7.5, 10, None)] == [0, 0, 1, 2, 3, 4, None]


def test_parse_errors() -> None:
    with pytest.raises(PollenError, match="invalid api key"):
        parse_forecast({"error": "invalid api key"}, TODAY)
    with pytest.raises(PollenError):
        parse_forecast([], TODAY)
    assert parse_forecast({}, TODAY)["allergens"] == []


async def test_request_carries_key_and_never_leaks_it() -> None:
    seen: dict[str, str] = {}

    def ok(req: httpx.Request) -> httpx.Response:
        seen.update(dict(req.url.params))
        return httpx.Response(200, json=RAW)

    async with client(ok) as c:
        f = await c.forecast(51.5074, -0.1278, TODAY)
    assert seen["apikey"] == "KEY123" and seen["country"] == "GB" and seen["latitude"] == "51.5074"
    assert len(f["allergens"]) == 2

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed for " + str(req.url))

    async with client(boom) as c:
        with pytest.raises(PollenError) as ei:
            await c.raw(51.0, 0.0)
    assert "KEY123" not in str(ei.value)

    async with client(lambda r: httpx.Response(403)) as c:
        with pytest.raises(PollenError, match="rejected"):
            await c.raw(51.0, 0.0)


async def test_missing_key_is_a_clear_error() -> None:
    async with PollenClient("") as c:
        with pytest.raises(PollenError, match="pollen_api_key"):
            await c.raw(51.0, 0.0)


async def test_assistant_pollen_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def near(request: Any, **kw: Any) -> dict[str, Any]:
        assert kw["place"] == "home"
        return {
            "place": "Home", "allergy_risk_label": ["high"],
            "allergens": [{"name": "Grasses", "levels": [3], "labels": ["high"]},
                          {"name": "Birch", "levels": [0], "labels": ["none"]}],
        }  # fmt: skip

    monkeypatch.setattr(assistant, "pollen_near", near)
    out = await assistant.run_tool(None, "pollen", {"place": "home"})  # type: ignore[arg-type]
    assert out["text"] == "Near Home, allergy risk today is high; pollen today: Grasses high."


class _FakeRedis:
    def __init__(self) -> None:
        self.d: dict[str, str] = {}

    async def get(self, k: str) -> str | None:
        return self.d.get(k)

    async def set(self, k: str, v: str, ex: int | None = None) -> None:
        self.d[k] = v


def test_endpoint_caches_sorts_and_needs_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app import pollen
    from app.main import app

    monkeypatch.setenv("API_TOKEN", "s3cret")
    monkeypatch.setenv("POLLEN_API_KEY", "k")
    calls = {"n": 0}

    class Fake:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        async def forecast(self, lat: float, lon: float, today: date, **kw: Any) -> dict[str, Any]:
            calls["n"] += 1
            return parse_forecast(RAW, today)

    async def fake_place(request: Any, place: str) -> tuple[str, float, float]:
        return "Home", 51.5074, -0.1278

    monkeypatch.setattr(pollen, "_client", lambda request: Fake())
    monkeypatch.setattr(pollen, "_place", fake_place)
    app.state.redis = _FakeRedis()
    c = TestClient(app)
    assert c.get("/v1/pollen/near?place=home").status_code == 401
    h = {"Authorization": "Bearer s3cret"}
    a = c.get("/v1/pollen/near?place=home", headers=h).json()
    b = c.get("/v1/pollen/near?place=home&hourly=true", headers=h).json()
    assert a["cached"] is False and b["cached"] is True and calls["n"] == 1
    assert [x["name"] for x in a["allergens"]] == ["Grasses", "Birch"]  # worst today first
    assert "allergy_risk_hourly" not in a and "allergy_risk_hourly" in b
    assert a["source"].startswith("Austrian Pollen")


def test_endpoint_without_key_is_503(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app

    monkeypatch.setenv("API_TOKEN", "s3cret")
    monkeypatch.delenv("POLLEN_API_KEY", raising=False)
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path)
    r = TestClient(app).get("/v1/pollen/near?place=home", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 503


def test_endpoint_takes_a_point_instead_of_a_place(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app import pollen
    from app.main import app

    monkeypatch.setenv("API_TOKEN", "s3cret")
    monkeypatch.setenv("POLLEN_API_KEY", "k")
    asked: list[tuple[float, float]] = []

    class Fake:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        async def forecast(self, lat: float, lon: float, today: date, **kw: Any) -> dict[str, Any]:
            asked.append((lat, lon))
            return parse_forecast(RAW, today)

    async def no_place(*a: Any, **k: Any) -> Any:
        raise AssertionError("a point needs no saved place")

    monkeypatch.setattr(pollen, "_client", lambda request: Fake())
    monkeypatch.setattr(pollen, "_place", no_place)
    monkeypatch.setattr(pollen, "place_or_default", no_place)
    app.state.redis = _FakeRedis()
    c = TestClient(app)
    h = {"Authorization": "Bearer s3cret"}
    ok = c.get("/v1/pollen/near?lat=51.5074&lon=-0.1278", headers=h)
    assert ok.status_code == 200 and ok.json()["place"] == "your location"
    assert asked == [(51.5074, -0.1278)]
    assert c.get("/v1/pollen/near?lat=51.5", headers=h).status_code == 422  # both or neither


def test_endpoints_answer_from_the_stored_lattice(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from app import pollen
    from app.main import app

    monkeypatch.setenv("API_TOKEN", "s3cret")
    monkeypatch.setenv("POLLEN_API_KEY", "k")
    now = datetime.now(UTC)
    fc = {"dates": ["2026-10-21"], "allergy_risk": [3], "allergy_risk_level": [1], "allergy_risk_label": ["low"],
          "allergens": [{"id": 1, "name": "Birch", "levels": [1], "labels": ["low"]}, {"id": 2, "name": "Grasses", "levels": [3], "labels": ["high"]}]}  # fmt: skip

    async def stored(engine: Any) -> list[dict[str, Any]]:
        return [{"lat": 52.5, "lon": -1.5, "forecast": fc, "fetched_at": now, "changed_at": now}]

    class NoLive:
        async def __aenter__(self) -> Self:
            raise AssertionError("a stored point should answer without calling the provider")

        async def __aexit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(pollen, "load_grid", stored)
    monkeypatch.setattr(pollen, "_client", lambda request: NoLive())
    app.state.engine = object()
    app.state.redis = _FakeRedis()
    c = TestClient(app)
    h = {"Authorization": "Bearer s3cret"}
    near = c.get("/v1/pollen/near?lat=52.4&lon=-1.4", headers=h).json()
    assert near["cached"] is True and near["grid"]["distance_km"] < 20
    assert [a["name"] for a in near["allergens"]] == ["Grasses", "Birch"]  # worst first
    grid = c.get("/v1/pollen/grid", headers=h).json()
    assert grid["count"] == 1 and grid["points"][0]["allergens"][0]["name"] == "Birch"
    assert c.get("/v1/pollen/grid").status_code == 401
