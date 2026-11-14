"""An example outside module for World Assistant: every five minutes it asks the module gate for your home's
location and your places (only if you agreed), works out how far a chosen place is from home, and publishes that
as a Home Assistant sensor. It also serves one small page.

Everything it reaches goes through the gate (WA_GATE, with WA_MODULE and WA_MODULE_TOKEN), so it has exactly
what you agreed to and nothing else. Standard library only.
"""

import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

GATE, MODULE, TOKEN = os.environ["WA_GATE"], os.environ["WA_MODULE"], os.environ["WA_MODULE_TOKEN"]
LAST: dict = {}


def gate(path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(GATE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-WA-Module": MODULE, "Authorization": f"Bearer {TOKEN}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def km(a: tuple, b: tuple) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def once() -> None:
    me = gate("/api/me")
    want = (me["settings"] or {}).get("place", "work")
    try:
        home = gate("/api/home")
        places = gate("/api/places")["places"]
    except urllib.error.HTTPError as exc:  # 403: you did not agree; a module must cope
        LAST.update(error=f"not shared ({exc.code})")
        return
    p = next((x for x in places if x["id"] == want), None)
    if p is None:
        LAST.update(error=f"no place called {want}")
        return
    d = round(km((home["lat"], home["lon"]), (p["lat"], p["lon"])), 1)
    gate("/api/ha/entity", {"component": "sensor", "object_id": "distance", "name": f"Home to {p['name']}",
                            "state": d, "unit": "km", "attributes": {"precision": home["precision"]}})
    LAST.update(place=p["name"], km=d, at=time.strftime("%H:%M"), error=None)


def loop() -> None:
    while True:
        try:
            once()
        except Exception as exc:  # noqa: BLE001 - keep going; the page shows the last problem
            LAST.update(error=str(exc)[:200])
        time.sleep(300)


class Page(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = ("<!doctype html><meta charset=utf-8><body style='font-family:sans-serif'><h1>Hello</h1><p>"
                + (f"{LAST['place']} is {LAST['km']} km from home (at {LAST['at']})." if LAST.get("km") is not None
                   else f"Nothing yet: {LAST.get('error') or 'starting'}.") + "</p>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    HTTPServer(("0.0.0.0", 8080), Page).serve_forever()
