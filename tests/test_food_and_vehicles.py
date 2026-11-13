"""The Food Standards Agency (hygiene ratings, alerts matched to the shopping) and tax and MOT (DVLA, DVSA), faked."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import httpx
import pytest

from app.modules.food import food as F
from app.modules.vehiclechecks import vehiclecheck as V


def test_hygiene_ratings_nearest_first_with_the_low_ones_named() -> None:
    F._cache.clear()

    def fake(req: httpx.Request) -> httpx.Response:
        assert req.headers["x-api-version"] == "2" and req.url.params["sortOptionKey"] == "Distance"
        return httpx.Response(200, json={"establishments": [
            {"FHRSID": 1, "BusinessName": "Corner Cafe", "BusinessType": "Restaurant/Cafe/Canteen", "AddressLine1": "1 High St",
             "PostCode": "KT3 4AA", "RatingValue": "5", "RatingDate": "2025-06-01T00:00:00", "Distance": 0.05,
             "geocode": {"latitude": "51.4", "longitude": "-0.25"}},
            {"FHRSID": 2, "BusinessName": "Kebab Hut", "RatingValue": "1", "RatingDate": "2026-02-01T00:00:00", "Distance": 0.2,
             "geocode": {"latitude": None, "longitude": None}},
            {"FHRSID": 3, "BusinessName": "New Bakery", "RatingValue": "AwaitingInspection", "RatingDate": "0001-01-01T00:00:00"}],
            "meta": {"totalCount": 31}})  # fmt: skip

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as c:
            return await F.hygiene(51.4, -0.25, 0.5, None, 10, c)

    d = asyncio.run(go())
    assert d["total"] == 31 and [p["name"] for p in d["places"]] == ["Corner Cafe", "Kebab Hut", "New Bakery"]
    assert d["places"][0]["url"] == "https://ratings.food.gov.uk/business/1" and d["places"][0]["rated_on"] == "2025-06-01"
    assert d["places"][2]["rated_on"] is None and d["places"][2]["rating_text"] == "awaiting inspection"
    assert F.hygiene_words(d) == ("Nearest rated: Corner Cafe: 5; Kebab Hut: 1; New Bakery: awaiting inspection. "
                                  "Rated 2 or lower: Kebab Hut.")


ALERT = {"notation": "FSA-AA-42-2026", "type": ["http://data.food.gov.uk/food-alerts/def/Alert", "http://data.food.gov.uk/food-alerts/def/AA"],
         "title": "Sainsbury's recalls Houmous because of undeclared sesame", "created": "2026-11-05",
         "alertURL": "https://www.food.gov.uk/news-alerts/alert/fsa-aa-42-2026", "reportingBusiness": {"commonName": "Sainsbury's"},
         "problem": [{"riskStatement": "Contains sesame, not on the label.", "allergen": [{"label": "Sesame"}]}],
         "productDetails": [{"productName": "Sainsbury's Reduced Fat Houmous", "packSizeDescription": "200g"}]}  # fmt: skip


def test_an_alert_read_and_matched_to_the_list_and_usual_products() -> None:
    a = F.alert_of(ALERT)
    assert a["kind"] == "Allergy alert" and a["allergens"] == ["Sesame"] and a["business"] == "Sainsbury's"
    assert a["products"][0]["text"] == "Sainsbury's Reduced Fat Houmous 200g"
    m = F.matches(a, ["houmous", "milk", "reduced fat milk"], ["Sainsbury's Reduced Fat Houmous 200g", "Sainsbury's Semi Skimmed Milk 4 Pints"])
    assert m == [{"what": "houmous", "from": "list", "how": "possible"},
                 {"what": "Sainsbury's Reduced Fat Houmous 200g", "from": "usual", "how": "likely"}]  # fmt: skip
    assert F.alert_of({"notation": "FSA-PRIN-01-2026", "title": "x"})["kind"] == "Product recall"


def _v(**checks: Any) -> dict[str, Any]:
    return {"id": "leaf", "name": "Leaf", "registration": "HV66BZD", "checks": {"checked_at": "2026-11-08T07:05:00+00:00", "errors": {}, **checks}}


def test_tax_and_mot_due_dates_count_down_and_warn() -> None:
    mot = V.mot_summary({"make": "NISSAN", "model": "LEAF", "hasOutstandingRecall": "No", "motTests": [
        {"completedDate": "2025-11-20T10:00:00.000Z", "testResult": "PASSED", "expiryDate": "2026-11-28", "odometerValue": "41000",
         "odometerUnit": "MI", "defects": [{"text": "Front tyre worn close to legal limit", "type": "ADVISORY", "dangerous": False}]},
        {"completedDate": "2024-11-18T10:00:00.000Z", "testResult": "FAILED", "defects": [{"text": "Brake pipe corroded", "type": "MAJOR"}]}]})  # fmt: skip
    assert mot["expires"] == "2026-11-28" and mot["last"]["advisories"] == ["Front tyre worn close to legal limit"]
    assert mot["tests"][1]["failures"] == ["Brake pipe corroded"]
    v = V.view(_v(dvla={"tax_status": "Taxed", "tax_due": "2027-03-01", "mot_status": "Valid", "mot_expires": "2026-11-28"}, mot=mot),
               today=date(2026, 11, 8))  # fmt: skip
    assert v["mot_days"] == 20 and v["tax_days"] == 113 and v["warnings"] == ["MOT due in 20 days (2026-11-28)"]
    s = V.words(v)
    assert s.startswith("Leaf (HV66BZD): tax Taxed, due 2027-03-01; MOT until 2026-11-28; last MOT 2025-11-20 passed with 1 advisories")
    new = V.mot_summary({"motTestDueDate": "2027-05-01", "motTests": []})
    assert new["expires"] == "2027-05-01" and new["last"] is None
    untaxed = V.view(_v(dvla={"tax_status": "Untaxed"}, mot={"recall_outstanding": True}), today=date(2026, 11, 8))
    assert untaxed["warnings"] == ["Not taxed", "A safety recall is outstanding: check with the maker"]


def test_both_sources_asked_with_their_keys_and_one_failing_is_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = {"dvla_api_key": "dk", "dvsa_mot_api_key": "mk", "dvsa_mot_client_id": "cid", "dvsa_mot_client_secret": "sec",
            "dvsa_mot_token_url": "https://login.microsoftonline.com/t/oauth2/v2.0/token"}  # fmt: skip
    monkeypatch.setattr(V, "read_secret", lambda k, d="": keys.get(k, d))
    V._token.clear()
    seen: list[str] = []

    def fake(req: httpx.Request) -> httpx.Response:
        seen.append(f"{req.method} {req.url.host}")
        if req.url.host == "login.microsoftonline.com":
            assert b"scope=https%3A%2F%2Ftapi.dvsa.gov.uk%2F.default" in req.content
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3599})
        if "driver-vehicle-licensing" in req.url.host:
            assert req.headers["x-api-key"] == "dk"
            return httpx.Response(200, json={"registrationNumber": "HV66BZD", "taxStatus": "Taxed", "taxDueDate": "2027-03-01",
                                             "motStatus": "Valid", "motExpiryDate": "2026-11-28", "make": "NISSAN"})  # fmt: skip
        assert req.headers["Authorization"] == "Bearer tok" and req.headers["X-API-Key"] == "mk"
        return httpx.Response(500, json={"errors": [{"detail": "Service unavailable"}]})

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as c:
            return await V.check_one("HV66BZD", c)

    got = asyncio.run(go())
    assert got["dvla"]["tax_due"] == "2027-03-01" and got["errors"] == {"mot": "DVSA answered HTTP 500: Service unavailable"}
    assert V.plate("hv66 bzd") == "HV66BZD"


DB = __import__("os").environ.get("WA_TEST_DATABASE_URL")


@pytest.mark.skipif(not DB, reason="WA_TEST_DATABASE_URL not set")
def test_a_registration_saved_checked_and_kept_with_the_vehicle(monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlalchemy as sa
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.main import app

    eng = sa.create_engine(DB)  # type: ignore[arg-type]
    with eng.begin() as c:
        c.execute(sa.text("DROP TABLE IF EXISTS vehicle"))
        c.execute(sa.text("CREATE TABLE vehicle (id text primary key, name text, registration text, checks jsonb)"))
        c.execute(sa.text("INSERT INTO vehicle (id, name) VALUES ('leaf', 'Leaf')"))

    async def fake_check(reg: str, *a: Any, **k: Any) -> dict[str, Any]:
        return {"registration": reg, "checked_at": "2026-11-08T07:05:00+00:00", "errors": {"mot": "no DVSA MOT key"},
                "dvla": {"tax_status": "Taxed", "tax_due": "2027-03-01", "mot_expires": "2026-11-28"}}  # fmt: skip

    monkeypatch.setattr(V, "check_one", fake_check)
    monkeypatch.setenv("API_TOKEN", "s3cret")
    app.state.engine = create_async_engine(DB, poolclass=NullPool)  # type: ignore[arg-type]
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer s3cret"
    try:
        assert c.put("/v1/vehicle-checks/leaf/registration", json={"registration": "ABCDEFGHIJ"}).status_code == 422
        r = c.put("/v1/vehicle-checks/leaf/registration", json={"registration": "hv66 bzd"}).json()
        assert r["registration"] == "HV66BZD" and r["tax_due"] == "2027-03-01" and "MOT" in r["text"]
        got = c.get("/v1/vehicle-checks").json()["vehicles"]
        assert got[0]["mot_due"] == "2026-11-28" and got[0]["errors"] == {"mot": "no DVSA MOT key"}
        assert c.post("/v1/vehicle-checks/leaf/check").status_code == 200
        assert c.put("/v1/vehicle-checks/leaf/registration", json={"registration": ""}).json()["registration"] is None
        assert c.get("/v1/vehicle-checks").json()["vehicles"] == []
        assert c.post("/v1/vehicle-checks/leaf/check").status_code == 404
    finally:
        with eng.begin() as conn:
            conn.execute(sa.text("DROP TABLE IF EXISTS vehicle"))
        eng.dispose()


def test_the_mot_login_uses_the_emails_scope_url_when_given(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = {"dvsa_mot_client_id": "cid", "dvsa_mot_client_secret": "sec", "dvsa_mot_scope_url": "https://tapi.dvsa.gov.uk/other",
            "dvsa_mot_token_url": "https://login.microsoftonline.com/t/oauth2/v2.0/token"}  # fmt: skip
    monkeypatch.setattr(V, "read_secret", lambda k, d="": keys.get(k, d))
    V._token.clear()

    def fake(req: httpx.Request) -> httpx.Response:
        assert b"scope=https%3A%2F%2Ftapi.dvsa.gov.uk%2Fother" in req.content
        return httpx.Response(200, json={"access_token": "tok2", "expires_in": 3599})

    async def go() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as c:
            return await V.mot_token(c)

    assert asyncio.run(go()) == "tok2"
    V._token.clear()
    keys["dvsa_mot_scope_url"] = "http://not-https.example"
    with pytest.raises(V.CheckError):
        asyncio.run(go())
    V._token.clear()
