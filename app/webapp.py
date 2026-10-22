"""What makes the web pages an installable app: the manifest, a service worker and a start page.

Add to Home Screen works on any phone (iOS from Safari, Android from Chrome). The service worker, which lets the
pages open when the server cannot be reached, only runs over HTTPS, for example through Tailscale
(docs/tailscale.md); on plain http on the home network the pages simply load from the server each time.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import text

from app.version import VERSION

router = APIRouter(include_in_schema=False)

MANIFEST = {
    "name": "World Assistant",
    "short_name": "World",
    "description": "Roads, fuel, buses, trains, events, pollen and energy: what is going on outside your home.",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#14231a",
    "theme_color": "#14231a",
    "icons": [
        {"src": "/static/app/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/static/app/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {"src": "/static/app/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
    "shortcuts": [
        {"name": "Buses", "url": "/bus"},
        {"name": "Fuel", "url": "/fuel"},
        {"name": "Map", "url": "/map"},
        {"name": "Settings", "url": "/settings"},
    ],
}

# Pages: network first, the last copy when offline. The API is never cached: answers must be live, and
# nothing about your places is kept in the browser's cache.
SERVICE_WORKER = """
const CACHE = 'wa-shell-%(version)s';
const SHELL = ['/settings', '/bus', '/trains', '/map', '/fuel', '/static/app/icon-192.png'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin || u.pathname.startsWith('/v1/') || u.pathname === '/mcp') return;
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok && r.type === 'basic') { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
    return r;
  }).catch(() => caches.match(e.request).then(r => r || caches.match('/settings'))));
});
"""


@router.get("/manifest.webmanifest")
async def manifest() -> Response:
    return Response(json.dumps(MANIFEST), media_type="application/manifest+json")


@router.get("/sw.js")
async def service_worker() -> Response:
    return Response(SERVICE_WORKER % {"version": VERSION}, media_type="text/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})  # fmt: skip


@router.get("/")
async def start(request: Request) -> RedirectResponse:
    """Open on the map once there is a home; before that, on Settings, where the setup wizard starts."""
    try:
        async with request.app.state.engine.connect() as conn:
            home = (await conn.execute(text("SELECT count(*) FROM place WHERE kind = 'home' OR id = 'home'"))).scalar_one()
    except Exception:  # noqa: BLE001 - no database yet: Settings explains what is wrong
        home = 0
    return RedirectResponse("/map" if home else "/settings", status_code=307)
