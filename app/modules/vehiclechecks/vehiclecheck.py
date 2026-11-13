"""Tax and MOT for your vehicles: DVLA's Vehicle Enquiry Service and DVSA's MOT History API.

Give a vehicle its registration on the Vehicles page. Once a week (and with Check now) World Assistant asks:

* **DVLA** (key `dvla_api_key`, sent as `x-api-key`): tax status and when it is due, MOT status and expiry, make,
  colour, first registration.
* **DVSA** (an API key `dvsa_mot_api_key` and a Microsoft Entra client: `dvsa_mot_client_id`, `dvsa_mot_client_secret`
  and `dvsa_mot_token_url`, all from DVSA's registration email): every MOT test with its result, mileage and the
  advisories and defects, when the first MOT is due for a new vehicle, and whether a safety recall is outstanding.

What they said is kept with the vehicle, so due dates count down without asking again. Either source works alone.

    GET  /v1/vehicle-checks                      every vehicle with a registration: tax, MOT, warnings
    PUT  /v1/vehicle-checks/{vehicle}/registration   {"registration": "AB12CDE"} (empty: forget it)
    POST /v1/vehicle-checks/{vehicle}/check      ask DVLA and DVSA now

    python -m app.modules.vehiclechecks.vehiclecheck REG               the probe: what each source says for a registration
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import UTC, date, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import require_token
from app.config import read_secret

log = logging.getLogger("wa.vehiclecheck")
router = APIRouter(prefix="/v1/vehicle-checks", tags=["vehicles"], dependencies=[Depends(require_token)])
DVLA = os.environ.get("WA_DVLA_URL", "https://driver-vehicle-licensing.api.gov.uk/vehicle-enquiry/v1/vehicles")
MOT = os.environ.get("WA_MOT_URL", "https://history.mot.api.gov.uk/v1/trade/vehicles/registration")
MOT_SCOPE = "https://tapi.dvsa.gov.uk/.default"
WARN_DAYS = 30
KEYS = ("dvla_api_key", "dvsa_mot_api_key", "dvsa_mot_client_id", "dvsa_mot_client_secret", "dvsa_mot_scope_url",
        "dvsa_mot_token_url")
_token: dict[str, Any] = {}


class CheckError(Exception):
    pass


def plate(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _err(r: httpx.Response, who: str) -> str:
    try:
        d = r.json()
        e = (d.get("errors") or [{}])[0] if isinstance(d, dict) else {}
        detail = e.get("detail") or e.get("title") or d.get("message") or d.get("errorMessage")
    except (ValueError, AttributeError):
        detail = None
    return f"{who} answered HTTP {r.status_code}" + (f": {detail}" if detail else "")


async def dvla(client: httpx.AsyncClient, reg: str) -> dict[str, Any]:
    key = read_secret("dvla_api_key")
    if not key:
        raise CheckError("no DVLA key (dvla_api_key): apply at developer-portal.driver-vehicle-licensing.api.gov.uk")
    r = await client.post(DVLA, json={"registrationNumber": reg},
                          headers={"x-api-key": key, "Accept": "application/json", "Content-Type": "application/json"})  # fmt: skip
    if r.status_code == 404:
        raise CheckError(f"DVLA has no vehicle {reg}")
    if r.status_code >= 400:
        raise CheckError(_err(r, "DVLA"))
    d = r.json()
    return {"make": d.get("make"), "colour": d.get("colour"), "fuel": d.get("fuelType"), "year": d.get("yearOfManufacture"),
            "first_registered": d.get("monthOfFirstRegistration"), "tax_status": d.get("taxStatus"),
            "tax_due": d.get("taxDueDate"), "mot_status": d.get("motStatus"), "mot_expires": d.get("motExpiryDate"),
            "marked_for_export": d.get("markedForExport")}  # fmt: skip


async def mot_token(client: httpx.AsyncClient) -> str:
    if _token.get("value") and _token.get("until", 0) - time.time() > 120:
        return str(_token["value"])
    cid, secret, url = (read_secret(k) for k in ("dvsa_mot_client_id", "dvsa_mot_client_secret", "dvsa_mot_token_url"))
    if not (cid and secret and url):
        raise CheckError("the DVSA MOT login is not set (Client ID, Client Secret and TokenURL from DVSA's email, on the "
                         "Keys page)")  # fmt: skip
    scope = (read_secret("dvsa_mot_scope_url") or MOT_SCOPE).strip()  # the email's "ScopeURL"; the usual one if not set
    if not url.startswith("https://") or not scope.startswith("https://"):
        raise CheckError("the DVSA TokenURL and ScopeURL should both start https://")
    r = await client.post(url, data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret, "scope": scope})
    if r.status_code >= 400:
        raise CheckError(f"DVSA's login (Microsoft) answered HTTP {r.status_code}: check the client id and secret")
    d = r.json()
    _token.update(value=d["access_token"], until=time.time() + float(d.get("expires_in") or 3000))
    return str(d["access_token"])


def mot_summary(d: dict[str, Any]) -> dict[str, Any]:
    tests = sorted(d.get("motTests") or [], key=lambda t: str(t.get("completedDate") or ""), reverse=True)

    def test(t: dict[str, Any]) -> dict[str, Any]:
        defects = t.get("defects") or t.get("rfrAndComments") or []
        return {"date": str(t.get("completedDate") or "")[:10] or None, "result": t.get("testResult"),
                "expires": t.get("expiryDate"), "odometer": t.get("odometerValue"), "unit": t.get("odometerUnit"),
                "advisories": [x.get("text") for x in defects if str(x.get("type")).upper() in ("ADVISORY", "MINOR")],
                "failures": [x.get("text") for x in defects if str(x.get("type")).upper() in ("FAIL", "MAJOR", "DANGEROUS", "PRS")],
                "dangerous": any(x.get("dangerous") for x in defects)}  # fmt: skip

    passed = next((t for t in tests if str(t.get("testResult")).upper() == "PASSED"), None)
    return {"model": d.get("model"), "make": d.get("make"), "first_used": d.get("firstUsedDate"),
            "recall_outstanding": d.get("hasOutstandingRecall"), "first_mot_due": d.get("motTestDueDate"),
            "expires": (passed or {}).get("expiryDate") or d.get("motTestDueDate"),
            "last": test(tests[0]) if tests else None, "tests": [test(t) for t in tests[:10]]}  # fmt: skip


async def mot(client: httpx.AsyncClient, reg: str) -> dict[str, Any]:
    key = read_secret("dvsa_mot_api_key")
    if not key:
        raise CheckError("no DVSA MOT key (dvsa_mot_api_key): register at documentation.history.mot.api.gov.uk")
    tok = await mot_token(client)
    r = await client.get(f"{MOT}/{reg}", headers={"Authorization": f"Bearer {tok}", "X-API-Key": key, "Accept": "application/json"})
    if r.status_code == 404:
        raise CheckError(f"DVSA has no MOT record for {reg}")
    if r.status_code >= 400:
        raise CheckError(_err(r, "DVSA"))
    return mot_summary(r.json())


async def check_one(reg: str, client: httpx.AsyncClient | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Both sources for one registration; each one's failure is noted, not fatal."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    out: dict[str, Any] = {"registration": reg, "checked_at": (now or datetime.now(UTC)).isoformat(), "errors": {}}
    try:
        for name, fn in (("dvla", dvla), ("mot", mot)):
            try:
                out[name] = await fn(client, reg)
            except CheckError as exc:
                out["errors"][name] = str(exc)
            except httpx.HTTPError as exc:
                out["errors"][name] = f"could not be reached ({type(exc).__name__})"
    finally:
        if own:
            await client.aclose()
    return out


def view(v: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """A vehicle with what was last heard: tax and MOT due dates, days left and warnings."""
    today = today or datetime.now(UTC).date()
    c = v.get("checks") or {}
    d, m = c.get("dvla") or {}, c.get("mot") or {}
    out: dict[str, Any] = {"id": v["id"], "name": v["name"], "registration": v.get("registration"), "checked_at": c.get("checked_at"),
                           "errors": c.get("errors") or {}, "make": d.get("make") or m.get("make"), "model": m.get("model"),
                           "colour": d.get("colour"), "tax_status": d.get("tax_status"), "tax_due": d.get("tax_due"),
                           "mot_status": d.get("mot_status"), "mot_due": d.get("mot_expires") or m.get("expires"),
                           "last_mot": m.get("last"), "recall_outstanding": m.get("recall_outstanding"), "warnings": []}  # fmt: skip

    def days(x: Any) -> int | None:
        try:
            return (date.fromisoformat(str(x)[:10]) - today).days
        except ValueError:
            return None

    out["tax_days"], out["mot_days"] = days(out["tax_due"]), days(out["mot_due"])
    w = out["warnings"]
    if out["tax_status"] in ("Untaxed", "Not Taxed for on Road Use"):
        w.append("Not taxed")
    elif out["tax_days"] is not None and out["tax_days"] <= WARN_DAYS and out["tax_status"] != "SORN":
        w.append("Tax " + ("ran out" if out["tax_days"] < 0 else f"due in {out['tax_days']} days" if out["tax_days"] else "due today") + f" ({out['tax_due']})")
    if out["mot_days"] is not None and out["mot_days"] <= WARN_DAYS:
        w.append("MOT " + ("ran out" if out["mot_days"] < 0 else f"due in {out['mot_days']} days" if out["mot_days"] else "due today") + f" ({out['mot_due']})")
    elif out["mot_status"] == "Not valid":
        w.append("No valid MOT")
    if out["recall_outstanding"] in (True, "Yes", "YES"):
        w.append("A safety recall is outstanding: check with the maker")
    return out


def words(v: dict[str, Any]) -> str:
    bits = []
    if v.get("tax_due") or v.get("tax_status"):
        bits.append(f"tax {v.get('tax_status') or ''}".strip() + (f", due {v['tax_due']}" if v.get("tax_due") else ""))
    if v.get("mot_due"):
        bits.append(f"MOT until {v['mot_due']}")
    lm = v.get("last_mot")
    if lm:
        bits.append(f"last MOT {lm['date']} {str(lm['result'] or '').lower()}" + (f" with {len(lm['advisories'])} advisories" if lm["advisories"] else "")
                    + (f" at {lm['odometer']} {str(lm.get('unit') or '').lower()}" if lm.get("odometer") else ""))
    s = f"{v['name']} ({v['registration']}): " + ("; ".join(bits) if bits else "nothing heard yet") + "."
    if v["warnings"]:
        s += " " + "; ".join(v["warnings"]) + "."
    if v["errors"]:
        s += " " + " ".join(f"{k.upper()}: {e}." for k, e in v["errors"].items())
    return s


async def _vehicles(engine: Any) -> list[dict[str, Any]]:
    async with engine.connect() as c:
        return [dict(r._mapping) for r in await c.execute(text("SELECT id, name, registration, checks FROM vehicle ORDER BY name"))]


async def check_all(engine: Any, only: str | None = None) -> list[dict[str, Any]]:
    out = []
    for v in await _vehicles(engine):
        if not v.get("registration") or (only and v["id"] != only):
            continue
        c = await check_one(v["registration"])
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE vehicle SET checks = CAST(:c AS jsonb) WHERE id = :i"), {"c": json.dumps(c, default=str), "i": v["id"]})
        out.append(view({**v, "checks": c}))
    return out


async def vehicle_check(ctx: dict[str, Any]) -> Any:
    from app.jobs import tracked

    async def run() -> Any:
        return [{"vehicle": v["id"], "warnings": v["warnings"], "errors": v["errors"]} for v in await check_all(ctx["engine"])]

    return await tracked(ctx["engine"], "vehicle_check", run)


@router.get("")
async def list_checks(request: Request) -> dict[str, Any]:
    vs = [view(v) for v in await _vehicles(request.app.state.engine) if v.get("registration")]
    for v in vs:
        v["text"] = words(v)
    return {"vehicles": vs, "keys": {k: bool(read_secret(k)) for k in KEYS}}


class RegIn(BaseModel):
    registration: str = Field("", max_length=12)


@router.put("/{vehicle_id}/registration")
async def set_registration(vehicle_id: str, body: RegIn, request: Request) -> dict[str, Any]:
    reg = plate(body.registration)
    if reg and not re.fullmatch(r"[A-Z0-9]{2,8}", reg):
        raise HTTPException(422, "A registration is 2 to 8 letters and numbers")
    async with request.app.state.engine.begin() as c:
        n = (await c.execute(text("UPDATE vehicle SET registration = :r, checks = NULL WHERE id = :i"), {"r": reg or None, "i": vehicle_id})).rowcount
    if not n:
        raise HTTPException(404, "no such vehicle")
    if not reg:
        return {"id": vehicle_id, "registration": None}
    got = await check_all(request.app.state.engine, vehicle_id)
    return {**got[0], "text": words(got[0])} if got else {"id": vehicle_id, "registration": reg}


@router.post("/{vehicle_id}/check")
async def check_now(vehicle_id: str, request: Request) -> dict[str, Any]:
    got = await check_all(request.app.state.engine, vehicle_id)
    if not got:
        raise HTTPException(404, "no such vehicle, or it has no registration")
    return {**got[0], "text": words(got[0])}


if __name__ == "__main__":  # the live check's probe: python -m app.modules.vehiclechecks.vehiclecheck REG
    import asyncio
    import sys

    if len(sys.argv) < 2 or not plate(sys.argv[1]):
        print("No registration given: nothing to probe")
        sys.exit(0)
    got = asyncio.run(check_one(plate(sys.argv[1])))
    print(json.dumps(got, indent=1, default=str)[:4000])
    v = view({"id": "probe", "name": "Vehicle", "registration": got["registration"], "checks": got})
    print(words(v))
    if len(got["errors"]) == 2 and not any(read_secret(k) for k in KEYS):
        print("(no keys set yet)")
    elif len(got["errors"]) == 2:
        print("FAILED: neither DVLA nor DVSA answered")
        sys.exit(1)
