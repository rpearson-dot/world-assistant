"""Rail departures: the shared model and the National Rail client."""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.integrations.rail.rdm import DEFAULT_BASE, RdmClient


def make_rail_client(settings: Settings, limiter: Any | None = None) -> RdmClient:
    return RdmClient(
        settings.nr_base_url or DEFAULT_BASE, settings.nr_access_token, limiter=limiter
    )
