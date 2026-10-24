"""Octopus Energy's public REST API: which tariff an account is on, and that tariff's unit rates.

Unit rates are public (no key). Finding the tariff from an account number needs the account's API key
(HTTP Basic, key as the username, empty password). Setting WA_OCTOPUS_TARIFF skips discovery entirely.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

BASE = "https://api.octopus.energy"
TARIFF_RE = re.compile(r"^E-(?P<reg>[12]R)-(?P<product>.+)-(?P<region>[A-P])$")


class OctopusError(Exception):
    pass


def product_of(tariff_code: str) -> str:
    """`E-1R-COSY-22-12-08-A` -> `COSY-22-12-08`."""
    m = TARIFF_RE.match(tariff_code.strip().upper())
    if not m:
        raise OctopusError(f"not an electricity tariff code: {tariff_code!r}")
    return m["product"]


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value)).astimezone(UTC)


def pick_tariff(account: dict[str, Any], now: datetime | None = None) -> str:
    """The current import tariff on an account body. Export (`-OUTGOING`/`EXPORT`) agreements are skipped."""
    now = now or datetime.now(UTC)
    found: list[tuple[datetime, str]] = []
    for prop in account.get("properties") or []:
        for point in prop.get("electricity_meter_points") or []:
            if point.get("is_export"):
                continue
            for ag in point.get("agreements") or []:
                code = str(ag.get("tariff_code") or "")
                if (
                    not TARIFF_RE.match(code.upper())
                    or "OUTGOING" in code.upper()
                    or "EXPORT" in code.upper()
                ):
                    continue
                start, end = _parse_time(ag.get("valid_from")), _parse_time(ag.get("valid_to"))
                if start and start > now or end and end <= now:
                    continue
                found.append((start or datetime.min.replace(tzinfo=UTC), code.upper()))
    if not found:
        raise OctopusError("no current electricity import agreement found on the account")
    return max(found)[1]


async def discover_tariff(client: httpx.AsyncClient, account: str, api_key: str) -> str:
    r = await client.get(f"{BASE}/v1/accounts/{account}/", auth=(api_key, ""))
    if r.status_code in (401, 403):
        raise OctopusError(
            "Octopus refused the API key (check octopus_api_key and the account number)"
        )
    r.raise_for_status()
    return pick_tariff(r.json())


def parse_rates(body: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in body.get("results") or []:
        start = _parse_time(item.get("valid_from"))
        inc = item.get("value_inc_vat")
        if start is None or inc is None:
            continue
        exc = item.get("value_exc_vat")
        out.append({"valid_from": start, "valid_to": _parse_time(item.get("valid_to")),
                    "pence_inc_vat": float(inc), "pence_exc_vat": None if exc is None else float(exc)})  # fmt: skip
    return out


async def fetch_rates(
    client: httpx.AsyncClient,
    tariff_code: str,
    now: datetime | None = None,
    days_back: int = 1,
    days_ahead: int = 3,
) -> list[dict[str, Any]]:
    """Unit rates from `days_back` ago to `days_ahead` ahead (Octopus publishes tomorrow's mid-afternoon)."""
    now = now or datetime.now(UTC)
    product = product_of(tariff_code)
    url = f"{BASE}/v1/products/{product}/electricity-tariffs/{tariff_code}/standard-unit-rates/"
    params: dict[str, Any] = {"period_from": (now - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "period_to": (now + timedelta(days=days_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "page_size": 1500}  # fmt: skip
    rows: list[dict[str, Any]] = []
    for _ in range(10):
        r = await client.get(url, params=params)
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise OctopusError("the rates response had no results list")
        rows += parse_rates(body)
        url, params = body.get("next"), {}
        if not url:
            break
    return rows

