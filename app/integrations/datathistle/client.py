"""Data Thistle: the UK's live-events listings (theatre, comedy, gigs, exhibitions, family days out, festivals).

Free tier, by registering at https://api.datathistle.com/ and requesting a key; the access token is kept in the
`datathistle_token` secret. `GET /v1/events?lat=&lon=&distance=` with `Authorization: Bearer <token>`, paged by the
`X-Next` header. Records follow Data Thistle's feed specification: an event has `schedules`, each at a `place` (with
`loc.latitude/longitude`) and with `performances` (`ts`, `tickets` with `min_price`/`max_price`, `links` of type
"booking").

The terms: show Data Thistle's logo with a link, and point people to its add-an-event and update-an-event pages; keep
nothing longer than 24 hours (so these are only ever asked live and cached briefly, never stored); do not visit the
links in its data programmatically. Not run against the live API from where this was written: the request
parameters beyond lat, lon and distance are not used, and dates and words are filtered here.

    docker compose exec api python -m app.integrations.datathistle.client [lat lon]     what it sends
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any, Self

import httpx

from app.integrations.fuelfinder.queries import haversine_km

BASE = "https://api.datathistle.com"
ATTRIBUTION = {"name": "Data Thistle", "url": "https://www.datathistle.com/",
               "add_event": "https://www.datathistle.com/", "update_event": "https://www.datathistle.com/"}  # fmt: skip
MAX_PAGES = 2  # each page is a request out of 1,000 a month
# Data Thistle's categories and tags, to the event types the events page filters by (Skiddle's codes)
TYPES = (("comedy", "COMEDY"), ("theatre", "THEATRE"), ("musical", "THEATRE"), ("dance", "THEATRE"),
         ("opera", "THEATRE"), ("gig", "LIVE"), ("music", "LIVE"), ("concert", "LIVE"), ("club", "CLUB"),
         ("exhibition", "EXHIB"), ("art", "ARTS"), ("kids", "KIDS"), ("family", "KIDS"), ("children", "KIDS"),
         ("festival", "FEST"), ("sport", "SPORT"), ("lgbt", "LGB"))  # fmt: skip


class DataThistleError(Exception):
    """Data Thistle could not be read: the message says why."""


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _type(raw: dict[str, Any]) -> str | None:
    words = " ".join([str(raw.get("category") or "")] + [str(t) for t in raw.get("tags") or []]).lower()
    return next((code for word, code in TYPES if word in words), None)


def _price(tickets: list[dict[str, Any]]) -> str | None:
    lows = [t["min_price"] for t in tickets if isinstance(t.get("min_price"), (int, float))]
    highs = [t.get("max_price") if isinstance(t.get("max_price"), (int, float)) else t.get("min_price")
             for t in tickets if isinstance(t.get("min_price"), (int, float))]  # fmt: skip
    if not lows:
        d = next((t.get("description") for t in tickets if t.get("description")), None)
        return str(d)[:80] if d else None
    lo, hi = min(lows), max(h for h in highs if h is not None)
    if hi == 0:
        return "Free"
    return f"£{lo:.2f}" if lo == hi else f"£{lo:.2f} to £{hi:.2f}"


def events_of(raw: dict[str, Any], first: date, last: date, origin: tuple[float, float] | None = None) -> list[dict[str, Any]]:
    """One row per day an event is on within [first, last], in the shape the other sources use."""
    out: list[dict[str, Any]] = []
    desc = raw.get("description") or next((d.get("description") for d in raw.get("descriptions") or [] if d.get("description")), None)
    image = raw.get("image") if isinstance(raw.get("image"), dict) else next(iter(raw.get("images") or []), None)
    for sch in raw.get("schedules") or []:
        place = sch.get("place") if isinstance(sch.get("place"), dict) else {}
        loc = place.get("loc") or {}
        lat, lon = _num(loc.get("latitude")), _num(loc.get("longitude"))
        dist = round(haversine_km(origin, (lat, lon)), 2) if origin and lat is not None and lon is not None else None
        seen: set[str] = set()
        for perf in sch.get("performances") or []:
            ts = str(perf.get("ts") or "")
            day = ts[:10]
            try:
                d = date.fromisoformat(day)
            except ValueError:
                continue
            if not first <= d <= last or day in seen:
                continue
            seen.add(day)
            props = perf.get("properties") or {}
            booking = next((x.get("url") for x in perf.get("links") or [] if x.get("type") == "booking" and x.get("url")), None)
            url = booking or raw.get("website")
            gone = "Cancelled" if props.get("performance.cancelled") else "Sold out" if props.get("performance.sold-out") else None
            out.append({
                "id": f"dt:{raw.get('event_id')}:{sch.get('place_id')}:{day}", "name": str(raw.get("name") or "")[:200],
                "date": day, "end_date": None,
                "doors_open": None if perf.get("time_unknown") or len(ts) <= 10 else ts[11:16],
                "type": _type(raw), "venue": place.get("name"), "town": place.get("town"),
                "postcode": place.get("postal_code"), "lat": lat, "lon": lon, "distance_km": dist,
                "price": _price(perf.get("tickets") or []), "min_age": props.get("event.minimum-age"), "going": None,
                "description": str(desc)[:300] if desc else None, "image": (image or {}).get("url"),
                "url": url if isinstance(url, str) and url.startswith("https://") else None,
                "availability": gone or perf.get("time_unknown"),
            })  # fmt: skip
    return out


class DataThistleClient:
    def __init__(self, token: str, base_url: str = BASE, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._token = token
        self.requests = 0  # made by this client, for the monthly allowance
        self._http = httpx.AsyncClient(base_url=base_url, timeout=20, transport=transport,
                                       headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})  # fmt: skip

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        if not self._token:
            raise DataThistleError("the datathistle_token secret is not set")
        self.requests += 1
        try:
            r = await self._http.get(url, params=params)
        except httpx.HTTPError as exc:
            raise DataThistleError(f"Data Thistle could not be reached ({type(exc).__name__})") from exc
        if r.status_code in (401, 403):
            raise DataThistleError("Data Thistle refused the token: request a new one at api.datathistle.com")
        if r.status_code == 429:
            raise DataThistleError("Data Thistle's usage limit is reached for now")
        if r.status_code >= 400:
            raise DataThistleError(f"Data Thistle answered HTTP {r.status_code}")
        return r

    async def raw(self, lat: float, lon: float, distance: float) -> Any:
        return (await self._get("/v1/events", {"lat": lat, "lon": lon, "distance": distance})).json()

    async def search(self, lat: float, lon: float, *, radius_km: float, first: date, last: date, keyword: str | None = None,
                     event_type: str | None = None, limit: int = 20) -> dict[str, Any]:  # fmt: skip
        """Events on within [first, last] near a point, one row per day, nearest-soonest order left to the caller."""
        r = await self._get("/v1/events", {"lat": lat, "lon": lon, "distance": radius_km})
        events: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            try:
                data = r.json()
            except ValueError as exc:
                raise DataThistleError("Data Thistle did not send JSON") from exc
            items = data if isinstance(data, list) else (data.get("events") or data.get("data") or []) if isinstance(data, dict) else []
            for raw in items:
                if isinstance(raw, dict):
                    events += events_of(raw, first, last, (lat, lon))
            nxt = r.headers.get("x-next")
            if not nxt or len(events) >= limit * 4:
                break
            r = await self._get(nxt)
        if keyword:
            k = keyword.lower()
            events = [e for e in events if k in (e["name"] or "").lower() or k in (e["description"] or "").lower()]
        if event_type:
            events = [e for e in events if e["type"] == event_type]
        events = [e for e in events if e["distance_km"] is None or e["distance_km"] <= radius_km * 1.6]  # its distance unit is unchecked
        return {"total": len(events), "events": events[:limit]}


async def _main(argv: list[str]) -> int:
    from app.config import get_settings

    lat, lon = (float(argv[1]), float(argv[2])) if len(argv) > 2 else (51.4027, -0.2560)
    async with DataThistleClient(get_settings().datathistle_token) as c:
        try:
            raw = await c.raw(lat, lon, 5)
            print("-- raw, first 3000 characters:")
            print(json.dumps(raw, indent=1, default=str)[:3000])
            from zoneinfo import ZoneInfo

            today = datetime.now(ZoneInfo("Europe/London")).date()
            got = await c.search(lat, lon, radius_km=5, first=today, last=today + timedelta(days=6))
            print(f"\n-- read here: {got['total']} event-days")
            print(json.dumps(got["events"][:5], indent=2, default=str))
        except DataThistleError as exc:
            print(f"FAILED: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv)))


# --- Keeping the token alive -------------------------------------------------------------------------------------
# An access token lasts 30 days; the refresh token renews it for up to a month after that, and each renewal returns a
# new pair (GET https://auth.datathistle.com/v1/refresh with the refresh token as the Bearer token, answering
# {"status": 200, "accessToken": ..., "refreshToken": ...}). Both are saved to the keys folder (as the Keys page
# saves them), which comes before any other copy. Renewed 25 days after the token was first seen or last renewed, and
# at once (at most hourly) when Data Thistle refuses it.
AUTH = "https://auth.datathistle.com/v1/refresh"
AUTH_KEY = "datathistle_auth"
RENEW_AFTER_DAYS = 25
RETRY_S = 3600


def _save_key(name: str, value: str) -> None:
    import os
    import tempfile

    from app import secretstore

    d = secretstore.keys_dir()
    if not d.is_dir() or not os.access(d, os.W_OK):
        raise DataThistleError("the new tokens cannot be saved: the keys folder is missing or not writable")
    fd, tmp = tempfile.mkstemp(dir=d, prefix=f".{name}.")
    with os.fdopen(fd, "w") as f:
        f.write(value)
    os.chmod(tmp, 0o600)
    os.replace(tmp, d / f"{name}.txt")
    secretstore.clear_cache()


async def _state(engine: Any) -> dict[str, Any]:
    from sqlalchemy import text

    async with engine.connect() as conn:
        v = (await conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": AUTH_KEY})).scalar()
    return (json.loads(v) if isinstance(v, str) else v) or {}


async def _put_state(engine: Any, value: dict[str, Any]) -> None:
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO app_setting (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), now()) "
                                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
                           {"k": AUTH_KEY, "v": json.dumps(value)})  # fmt: skip


async def renew(engine: Any, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Swap the refresh token for a new pair and save both. Raises DataThistleError, and records why."""
    from app.config import get_settings

    now = datetime.now(UTC)
    st = await _state(engine)
    st["tried_at"] = now.isoformat()
    refresh = get_settings().datathistle_refresh_token
    try:
        if not refresh:
            raise DataThistleError("no refresh token saved (datathistle_refresh_token): renew it by hand on your Data Thistle account page")
        try:
            async with httpx.AsyncClient(timeout=20, transport=transport) as c:
                r = await c.get(AUTH, headers={"Authorization": f"Bearer {refresh}", "Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise DataThistleError(f"Data Thistle's renewal could not be reached ({type(exc).__name__})") from exc
        if r.status_code in (401, 403):
            raise DataThistleError("Data Thistle refused the refresh token (it lasts a month after the access token "
                                   "expires): make a new pair on your account page")  # fmt: skip
        if r.status_code >= 400:
            raise DataThistleError(f"Data Thistle's renewal answered HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError as exc:
            raise DataThistleError("Data Thistle's renewal did not send JSON") from exc
        access, new_refresh = body.get("accessToken"), body.get("refreshToken")
        if not (isinstance(access, str) and access and isinstance(new_refresh, str) and new_refresh):
            raise DataThistleError(f"Data Thistle's renewal did not include both tokens (it sent {sorted(body)})")
        _save_key("datathistle_refresh_token", new_refresh)  # the refresh first: losing it loses the chain
        _save_key("datathistle_token", access)
    except DataThistleError as exc:
        st["error"] = str(exc)
        await _put_state(engine, st)
        raise
    st.update(renewed_at=now.isoformat(), error=None, first_seen=st.get("first_seen") or now.isoformat())
    await _put_state(engine, st)
    return {"renewed": True, "at": st["renewed_at"]}


async def keep_alive(engine: Any, refused: bool = False, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Renew when the token is 25 days old (from when it was first seen or last renewed), or now if it was refused
    (not more than hourly). Notes when a token is first seen. Never raises: the outcome is returned and recorded."""
    from app.config import get_settings

    if not get_settings().datathistle_token:
        return {"renewed": False, "why": "no token saved"}
    now = datetime.now(UTC)
    st = await _state(engine)
    if not st.get("first_seen"):
        st["first_seen"] = now.isoformat()
        await _put_state(engine, st)
    since = datetime.fromisoformat(st.get("renewed_at") or st["first_seen"])
    due = now - since >= timedelta(days=RENEW_AFTER_DAYS)
    tried = st.get("tried_at")
    if refused and tried and (now - datetime.fromisoformat(tried)).total_seconds() < RETRY_S:
        return {"renewed": False, "why": "tried within the hour", "error": st.get("error")}
    if not (due or refused):
        return {"renewed": False, "why": f"renews on {(since + timedelta(days=RENEW_AFTER_DAYS)).date().isoformat()}"}
    try:
        return await renew(engine, transport)
    except DataThistleError as exc:
        return {"renewed": False, "error": str(exc)}


async def datathistle_renew(ctx: dict[str, Any]) -> Any:
    """The worker's daily check of the token."""
    from app.jobs import tracked

    async def run() -> Any:
        return await keep_alive(ctx["engine"])

    return await tracked(ctx["engine"], "datathistle_renew", run)
