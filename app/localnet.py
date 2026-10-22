"""Is this request from the local network? If so, the web pages and API need no token.

A token on every page is unfriendly on the home network, so a request counts as local, and is let in
without one, only when all of these hold (WA_LOCAL_NO_TOKEN=false turns it off entirely):

* it reached this server directly from a private, loopback, link-local or Tailscale (100.64.0.0/10)
  address. Anything that came through a reverse proxy (a Forwarded / X-Forwarded-* / X-Real-IP header) is
  never local, because the proxy could be relaying the internet;
* the Host it asked for is an IP address, ``localhost``, a single-label name (``nas``), a home-network name
  (``.local``, ``.lan``, ``.home``, ``.home.arpa``, ``.internal``, ``.localdomain``, ``.ts.net``) or one of
  WA_LOCAL_HOSTS. A web page on someone else's domain cannot then re-point its name at this server
  (DNS rebinding) and read it;
* a change (anything but GET, HEAD, OPTIONS) comes from this server's own pages: a browser's Origin, when
  sent, must be this host, and Sec-Fetch-Site must not say cross-site. Another site open in the same
  browser cannot submit to it.

Never expose the port to the internet: with port forwarding, Docker can present an outside connection as
coming from its own gateway, which is a private address.
"""

from __future__ import annotations

import socket
import time
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from urllib.parse import urlsplit

from starlette.requests import HTTPConnection

from app.config import get_settings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip")
HOME_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain", ".ts.net")
TAILSCALE = ip_network("100.64.0.0/10")


def _peer(conn: HTTPConnection) -> IPv4Address | IPv6Address | None:
    host = conn.client.host if conn.client else None
    try:
        ip = ip_address(host or "")
    except ValueError:
        return None
    if isinstance(ip, IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def private_peer(conn: HTTPConnection) -> bool:
    ip = _peer(conn)
    if ip is None:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or (isinstance(ip, IPv4Address) and ip in TAILSCALE)


def host_name(raw: str) -> str:
    """The name part of a Host header: no port, no IPv6 brackets, lower case."""
    raw = raw.strip().lower()
    if raw.startswith("["):
        return raw[1 : raw.find("]")] if "]" in raw else raw[1:]
    return raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw


def home_host(raw: str, extra: str = "") -> bool:
    name = host_name(raw).rstrip(".")
    if not name:
        return False
    try:
        ip_address(name)
        return True
    except ValueError:
        pass
    allowed = {h.strip().lower() for h in extra.split(",") if h.strip()}
    return name == "localhost" or name in allowed or "." not in name or name.endswith(HOME_SUFFIXES)


def same_origin(conn: HTTPConnection, method: str) -> bool:
    if method.upper() in SAFE_METHODS:
        return True
    if conn.headers.get("sec-fetch-site", "").lower() in ("cross-site", "same-site"):
        return False
    origin = conn.headers.get("origin")
    if origin is None:
        return True  # not a browser (curl, a script, Home Assistant)
    return urlsplit(origin).netloc.lower() == conn.headers.get("host", "").strip().lower()


def is_local(conn: HTTPConnection, method: str = "GET") -> bool:
    s = get_settings()
    if not s.local_no_token:
        return False
    if any(h in conn.headers for h in PROXY_HEADERS):
        return False
    return private_peer(conn) and home_host(conn.headers.get("host", ""), s.local_hosts) and same_origin(conn, method)


# --- Tailscale (docker-compose.tailscale.yml) -----------------------------------------------------------
# The sidecar answers https://<name>.<tailnet>.ts.net and passes requests on to the api, adding the tailnet
# user's identity. Such a request is a proxied one, so is_local() says no; instead:
#   * pages from the tailnet hide the token box (a page holds no data);
#   * the API answers without a token only the browser's own same-origin fetches from those pages, so the
#     web interface works from your phone while Siri Shortcuts, MCP clients and scripts still need the token.

_proxy_cache: tuple[float, str, frozenset[str]] = (0.0, "", frozenset())


def _proxy_ips(name: str) -> frozenset[str]:
    """The sidecar's addresses on the stack's network, looked up at most once a minute."""
    global _proxy_cache
    at, cached_name, ips = _proxy_cache
    if cached_name == name and time.monotonic() - at < 60:
        return ips
    try:
        ips = frozenset(str(info[4][0]) for info in socket.getaddrinfo(name, None))
    except OSError:
        ips = frozenset()
    _proxy_cache = (time.monotonic(), name, ips)
    return ips


def from_tailnet(conn: HTTPConnection) -> bool:
    """Came through this stack's Tailscale sidecar, from a signed-in tailnet user."""
    s = get_settings()
    if not (s.tailscale_proxy and s.tailnet_web_no_token):
        return False
    ip = _peer(conn)
    return ip is not None and str(ip) in _proxy_ips(s.tailscale_proxy) and bool(conn.headers.get("tailscale-user-login"))


def tailnet_page_fetch(conn: HTTPConnection, method: str = "GET") -> bool:
    """A fetch made by one of this server's pages, in a browser on the tailnet.

    Sec-Fetch-Site is set by the browser itself and a page cannot change it, so "same-origin" means the
    request came from this server's own page. Scripts and Shortcuts do not send it, so they need the token.
    (A tailnet device that sends the header by hand gets in; it is already on your tailnet.)
    """
    return from_tailnet(conn) and conn.headers.get("sec-fetch-site", "").lower() == "same-origin"


def page_without_token(conn: HTTPConnection) -> bool:
    """Serve a page with the token box hidden: on the home network, or in a browser on the tailnet."""
    return is_local(conn) or from_tailnet(conn)


# Put on every page served to a local browser: the pages already send whatever is in their token box, so a
# placeholder is stored (only when there is no real token) and the box is hidden; the API lets it through.
PAGE_SNIPPET = (
    "<script>try{if(!sessionStorage.getItem('wa_token'))sessionStorage.setItem('wa_token','local')}catch(e){}"
    "</script><style>#t{display:none}.bar:has(>#t):not(:has(>:not(#t):not(#go))){display:none}</style>"
)
