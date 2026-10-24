"""Octopus's GraphQL API (Kraken): Saving Sessions.

The queries and the two hosts are the ones the BottlecapDave Home Assistant integration uses
(https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy). They are not an official public contract, so
every parser here tolerates missing fields, and `python -m app.energy.probe` prints what Octopus really sends.

Read-only: this module never joins a session. Joining stays in the Octopus app (or a Home Assistant automation).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.energy.octopus import OctopusError

TOKEN_HOST = "https://api.oegb-kraken.energy"
BACKEND_HOST = "https://api.backend.octopus.energy"

TOKEN_MUTATION = 'mutation { obtainKrakenToken(input: { APIKey: "%s" }) { token } }'
SESSIONS_QUERY = """query { savingSessions {
  events(includeDev: false) { id code rewardPerKwhInOctoPoints startAt endAt devEvent
    targetRegion { regionId } eventType capacityStatus }
  account(accountNumber: "%s") { signedUpMeterPoint { regionId } hasJoinedCampaign tokenBalance
    joinedEvents { eventId startAt endAt rewardGivenInOctoPoints eventType } } } }"""

SAVING_TYPES = {"TURN_DOWN"}



def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def _graphql(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise OctopusError("Octopus GraphQL returned something that was not JSON")
    if body.get("errors") and not body.get("data"):
        message = (
            str((body["errors"][0] or {}).get("message", "error"))
            if isinstance(body["errors"], list)
            else "error"
        )
        raise OctopusError(f"Octopus GraphQL refused the request: {message[:200]}")
    return body.get("data") or {}


async def token(client: httpx.AsyncClient, api_key: str) -> str:
    r = await client.post(
        f"{TOKEN_HOST}/v1/graphql/", json={"query": TOKEN_MUTATION % api_key.replace('"', "")}
    )
    r.raise_for_status()
    t = ((_graphql(r.json()).get("obtainKrakenToken")) or {}).get("token")
    if not t:
        raise OctopusError("Octopus did not return a token (check the octopus_api_key secret)")
    return str(t)


def parse_saving(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Available and joined events as session rows. `joined` is true where the account has joined the event."""
    block = data.get("savingSessions") or {}
    account = block.get("account") or {}
    joined = {
        str(j.get("eventId")): j for j in account.get("joinedEvents") or [] if isinstance(j, dict)
    }
    rows: dict[str, dict[str, Any]] = {}

    def add(
        kind_type: str | None,
        code: str,
        start: Any,
        end: Any,
        points: Any,
        regions: list[str] | None,
        is_joined: bool,
    ) -> None:
        s, e = _time(start), _time(end)
        if not code or s is None or e is None or e <= s:
            return
        kind = "saving" if (kind_type or "TURN_DOWN") in SAVING_TYPES else "boost"
        rows[f"{kind}:{code}"] = {"kind": kind, "code": code, "start_at": s, "end_at": e,
                                  "octopoints_per_kwh": int(points) if isinstance(points, (int, float)) else None,
                                  "joined": is_joined, "regions": ",".join(regions) if regions else None,
                                  "event_type": kind_type}  # fmt: skip

    for ev in block.get("events") or []:
        if not isinstance(ev, dict) or ev.get("devEvent"):
            continue
        regions = [
            str(r.get("regionId"))
            for r in ev.get("targetRegion") or []
            if isinstance(r, dict) and r.get("regionId")
        ]
        add(ev.get("eventType"), str(ev.get("code") or ev.get("id") or ""), ev.get("startAt"), ev.get("endAt"),
            ev.get("rewardPerKwhInOctoPoints"), regions, str(ev.get("id")) in joined)  # fmt: skip
    # joined events that are no longer in the available list (already over) are still worth knowing
    known = {r["code"] for r in rows.values()}
    for event_id, j in joined.items():
        if event_id not in known and not any(
            str(ev.get("id")) == event_id
            for ev in block.get("events") or []
            if isinstance(ev, dict)
        ):
            add(
                j.get("eventType"),
                event_id,
                j.get("startAt"),
                j.get("endAt"),
                j.get("rewardGivenInOctoPoints"),
                None,
                True,
            )
    return list(rows.values())


async def fetch_saving(
    client: httpx.AsyncClient, api_key: str, account: str
) -> list[dict[str, Any]]:
    t = await token(client, api_key)
    r = await client.post(f"{BACKEND_HOST}/v1/graphql/", json={"query": SESSIONS_QUERY % account.replace('"', "")},
                          headers={"Authorization": t})  # fmt: skip
    r.raise_for_status()
    return parse_saving(_graphql(r.json()))

