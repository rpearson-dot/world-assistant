"""Comfort checks for a running stack: do the pages load, does every endpoint answer without a 500?

    docker compose exec -T api python -m app.smoke            # read-only; safe on your real stack
    docker compose exec -T api python -m app.smoke --seed     # also saves, queries and deletes a test place

Run by scripts/deploy.sh after every deploy, and by CI against a freshly built stack. Uses only the
standard library. Exits 1 if anything failed.

What counts as a failure:
  * a page that does not load, still has template placeholders, or lacks the navigation
  * a /v1 route that answers from outside without the token, or a 500 (or any 5xx other than a deliberate JSON
    502/503 such as "key not set" or "upstream unavailable") from ANY GET route in the OpenAPI list
  * a route that takes longer than 30 seconds, or claims JSON but returns something else
  * with --seed: the test place not saving, or the place-based queries not answering as expected
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

PAGES = ("/status", "/bus", "/trains", "/fuel", "/events", "/pollen", "/places", "/energy", "/ha")
SEED = "smoke-test-place"
SKIP_SWEEP = {"/mcp", "/healthz", "/readyz", "/v1/backups/{name}"}  # checked on their own, or a download
TIMEOUT = 30.0
HEARTBEAT_WAIT = int(os.environ.get("WA_SMOKE_HEARTBEAT_WAIT", "60"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a: Any, **k: Any) -> None:
        return None


_opener = urllib.request.build_opener(_NoRedirect)


# Makes a request look as if it came from the internet through a proxy (TEST-NET-3, documentation only), so
# the token checks still mean something now that local requests need no token.
OUTSIDE = {"X-Forwarded-For": "203.0.113.9"}


def fetch(base: str, path: str, token: str | None = None, method: str = "GET",
          body: Any = None, extra: dict[str, str] | None = None) -> tuple[int, str, str]:  # fmt: skip
    """(status, content type, body). Never raises for HTTP errors; a timeout or refusal gives status 0."""
    headers = {"Accept": "*/*", **(extra or {})}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base.rstrip("/") + path, data=data, headers=headers, method=method)
    try:
        with _opener.open(req, timeout=TIMEOUT) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, "", f"{type(e).__name__}: {e}"


class Report:
    def __init__(self, out: Callable[[str], None] = print) -> None:
        self.out, self.failed, self.total = out, 0, 0

    def check(self, ok: bool, what: str, detail: str = "") -> bool:
        self.total += 1
        if not ok:
            self.failed += 1
        self.out(f"{'ok  ' if ok else 'FAIL'} {what}" + (f"  ({detail})" if detail else ""))
        return ok


def _deliberate(status: int, body: str) -> bool:
    """A 502 or 503 that carries a JSON explanation is the app saying 'not now', not crashing."""
    if status not in (502, 503):
        return False
    try:
        return isinstance(json.loads(body), dict) and "detail" in json.loads(body)
    except ValueError:
        return False


def _fill(path: str) -> str:
    out = path
    while "{" in out:
        a, b = out.index("{"), out.index("}")
        out = out[:a] + "smoke-x" + out[b + 1 :]
    return out


def run(base: str, token: str, *, seed: bool = False, out: Callable[[str], None] = print) -> int:
    r = Report(out)

    # health and pages
    for path in ("/healthz", "/readyz"):
        s, _, b = fetch(base, path)
        r.check(s == 200, f"GET {path}", f"HTTP {s}" if s != 200 else "")
    for path in PAGES:
        s, ct, b = fetch(base, path)
        good = s == 200 and "text/html" in ct and "<nav>" in b and "<script>" in b
        good = good and "/*BASE_CSS*/" not in b and "<!--NAV-->" not in b
        r.check(good, f"page {path}", f"HTTP {s}" if not good else "")

    # the token is required from outside the local network
    s, _, _ = fetch(base, "/v1/status", extra=OUTSIDE)
    r.check(s == 401, "no token is refused from outside", f"HTTP {s}")
    s, _, _ = fetch(base, "/v1/status", "not-the-token", extra=OUTSIDE)
    r.check(s == 401, "wrong token is refused from outside", f"HTTP {s}")
    s, _, _ = fetch(base, "/v1/places/x", None, "DELETE", extra={"Origin": "http://elsewhere.example"})
    r.check(s == 401, "another site's page cannot change anything", f"HTTP {s}")
    host = urllib.parse.urlsplit(base).hostname or ""
    if host in ("127.0.0.1", "localhost", "::1") and os.environ.get("WA_LOCAL_NO_TOKEN", "true").lower() != "false":
        s, _, _ = fetch(base, "/v1/status")
        r.check(s == 200, "the local network needs no token", f"HTTP {s}")
    s, _, b = fetch(base, "/v1/status", token)
    r.check(s == 200, "GET /v1/status with the token", f"HTTP {s}")
    if s == 200:
        try:
            hb = json.loads(b).get("worker_heartbeat")
            # A worker that has only just started has not beaten yet: give it up to a minute.
            for _ in range(HEARTBEAT_WAIT // 3):
                if hb and not str(hb).startswith("error"):
                    break
                time.sleep(3)
                hb = json.loads(fetch(base, "/v1/status", token)[2]).get("worker_heartbeat")
            r.check(bool(hb) and not str(hb).startswith("error"), "worker heartbeat present", str(hb))
        except ValueError:
            r.check(False, "/v1/status returns JSON")

    # seed a place so place-based queries have something to answer
    place = None
    if seed:
        s, _, b = fetch(base, f"/v1/places/{SEED}", token, "PUT",
                        {"name": "Smoke test place", "kind": "other", "lat": 51.5074, "lon": -0.1278})  # fmt: skip
        if r.check(s == 200, "save a test place", f"HTTP {s}: {b[:120]}" if s != 200 else ""):
            place = SEED

    # every GET route from the OpenAPI list: nothing may 500
    s, _, b = fetch(base, "/openapi.json")
    if not r.check(s == 200, "GET /openapi.json", f"HTTP {s}"):
        return 1
    spec = json.loads(b)
    known = {"place": place, "lat": "51.5074", "lon": "-0.1278"}
    swept = 0
    for path, ops in sorted(spec["paths"].items()):
        if "get" not in ops or path in SKIP_SWEEP:
            continue
        query = {}
        for p in ops["get"].get("parameters", []):
            if p["in"] == "query" and known.get(p["name"]):
                query[p["name"]] = known[p["name"]]
        url = _fill(path) + ("?" + urllib.parse.urlencode(query) if query else "")
        s, ct, b = fetch(base, url, token)
        swept += 1
        ok = 0 < s < 500 or _deliberate(s, b)
        why = "" if ok else (b[:100] if s else b)
        if ok and s == 200 and "json" in ct:
            try:
                json.loads(b)
            except ValueError:
                ok, why = False, "claims JSON but is not"
        r.check(ok, f"GET {url}", f"HTTP {s} {why}".strip() if not ok else f"HTTP {s}")
    r.check(swept >= 10, f"swept {swept} GET routes", "the route list looks too short" if swept < 10 else "")

    # with a place: the queries that depend on one must answer properly
    if place:
        s, _, b = fetch(base, "/v1/places", token)
        r.check(s == 200 and place in b, "the test place is listed")
        for path in (f"/v1/fuel/near?place={place}", "/v1/fuel/near"):  # the second one uses the default
            s, _, _ = fetch(base, path, token)
            r.check(s == 200, f"GET {path}", f"HTTP {s}")
        for path in (f"/v1/pollen/history?place={place}", "/v1/pollen/history"):
            s, _, b = fetch(base, path, token)
            r.check(s == 200 and '"days"' in b, f"GET {path}", f"HTTP {s}")
        for path in (f"/v1/events/near?place={place}", f"/v1/pollen/near?place={place}"):
            s, _, b = fetch(base, path, token)
            r.check(s == 200 or _deliberate(s, b), f"GET {path}", f"HTTP {s}")
        s, _, b = fetch(base, "/v1/ask", token)
        r.check(s == 200 and "pollen" in b and "fuel_cheapest" in b, "assistant lists its tools", f"HTTP {s}")
        s, _, _ = fetch(base, f"/v1/places/{place}", token, "DELETE")
        r.check(s in (200, 204), "delete the test place", f"HTTP {s}")

    r.out(f"\n{r.total - r.failed} of {r.total} checks passed" + ("" if not r.failed else f", {r.failed} FAILED"))
    return 1 if r.failed else 0


def main(argv: list[str]) -> int:
    base = os.environ.get("WA_SMOKE_URL", "http://127.0.0.1:8000")
    token = os.environ.get("WA_SMOKE_TOKEN", "")
    if not token:
        from app.config import get_settings

        token = get_settings().api_token
    if not token:
        print("No api token found (secret api_token, or WA_SMOKE_TOKEN).")
        return 2
    return run(base, token, seed="--seed" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
