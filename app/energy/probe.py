"""Show what Octopus really sends for Saving Sessions and Free Electricity, so the parsers can be checked.

    docker compose exec worker python -m app.energy.probe

Prints the raw responses with the account number and any token blanked out. Safe to paste.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from typing import Any

import httpx

from app.config import get_settings
from app.energy import kraken
from app.energy.octopus import OctopusError


def redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "<hidden>")
    return re.sub(r"A-[0-9A-F]{8}", "A-<hidden>", text)


def show(title: str, value: Any, secrets: list[str]) -> None:
    body = value if isinstance(value, str) else json.dumps(value, indent=1, default=str)
    print(f"\n== {title} ==\n{redact(body, secrets)[:6000]}")


async def main() -> int:
    s = get_settings()
    key, account = s.octopus_api_key, s.octopus_account.strip()
    secrets = [key, account]
    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": "world-assistant (self-hosted)"}
    ) as c:
        if key and account:
            try:
                t = await kraken.token(c, key)
                secrets.append(t)
                show("token", "obtained", secrets)
                r = await c.post(
                    f"{kraken.BACKEND_HOST}/v1/graphql/",
                    json={"query": kraken.SESSIONS_QUERY % account},
                    headers={"Authorization": t},
                )
                show(f"saving sessions raw (HTTP {r.status_code})", r.text, secrets)
                if r.status_code == 200:
                    show(
                        "saving sessions as parsed",
                        kraken.parse_saving(r.json().get("data") or {}),
                        secrets,
                    )
            except (httpx.HTTPError, OctopusError, ValueError) as exc:
                show("saving sessions failed", f"{type(exc).__name__}: {exc}", secrets)
        else:
            show(
                "saving sessions",
                "skipped: needs WA_OCTOPUS_ACCOUNT and the octopus_api_key secret",
                secrets,
            )
        try:
            r = await c.get(s.energy_free_url)
            show(f"free electricity raw (HTTP {r.status_code})", r.text[:3000], secrets)
            if r.status_code == 200:
                show("free electricity as parsed", kraken.parse_free(r.json()), secrets)
        except (httpx.HTTPError, OctopusError, ValueError) as exc:
            show("free electricity failed", f"{type(exc).__name__}: {exc}", secrets)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
