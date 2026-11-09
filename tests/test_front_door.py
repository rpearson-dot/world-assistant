"""The stack's front door (wa-proxy, deploy/proxy/Caddyfile): through it, who may skip the token is decided by who
connected to the proxy, exactly as without it; its headers are believed only from the proxy itself."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.config import get_settings

from .test_localnet import api

ROOT = Path(__file__).resolve().parent.parent
FRONT = ("172.20.0.2", 40000)
CADDY = {"x-forwarded-for": "192.168.1.20", "x-forwarded-proto": "http", "x-forwarded-host": "192.168.1.10:8010"}  # Caddy adds these


@pytest.fixture(autouse=True)
def front(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app import localnet

    monkeypatch.setenv("API_TOKEN", "tok")
    monkeypatch.setenv("WA_FRONT_PROXY", "wa-proxy")
    monkeypatch.setenv("WA_TAILSCALE_PROXY", "wa-tailscale")
    get_settings.cache_clear()
    ips = {"wa-proxy": frozenset({"172.20.0.2"}), "wa-tailscale": frozenset({"172.20.0.9"})}
    monkeypatch.setattr(localnet, "_proxy_ips", lambda name: ips.get(name, frozenset()))
    yield
    get_settings.cache_clear()


def through(peer: str, hops: str = "", client: tuple[str, int] = FRONT, host: str = "192.168.1.10:8010", **extra: str) -> int:
    h = {**CADDY, "x-wa-peer": peer, "x-wa-hops": hops, **{k.replace("_", "-"): v for k, v in extra.items()}}
    return TestClient(api(), client=client, base_url=f"http://{host}").get("/v1/thing", headers=h).status_code


def test_the_home_network_through_the_front_door_needs_no_token() -> None:
    assert through("192.168.1.20") == 200
    assert through("172.18.0.1") == 200  # Docker's gateway, as the published port shows a LAN client


def test_the_internet_and_other_proxies_through_it_still_need_the_token() -> None:
    assert through("81.2.69.160") == 401
    assert through("192.168.1.20", hops="81.2.69.160") == 401  # Traefik, or anything else, came first
    assert through("192.168.1.20", hops="81.2.69.160", authorization="Bearer tok") == 200


def test_its_headers_count_only_from_the_proxy_itself() -> None:
    # A client on the stack's network (or one that reaches the api some other way) cannot claim to be at home
    assert through("192.168.1.20", client=("81.2.69.160", 1)) == 401
    assert through("192.168.1.20", client=("172.20.0.7", 1)) == 401  # not the proxy: Caddy's X-Forwarded-For counts


def test_the_tailnet_through_the_front_door() -> None:
    # Tailscale's serve adds X-Forwarded-For with the tailnet device, which the front door reports in X-WA-Hops
    ts = {"hops": "100.101.2.3", "host": "world-assistant.tail1234.ts.net"}
    tailnet = {"tailscale_user_login": "me@example.com", "sec_fetch_site": "same-origin"}
    assert through("172.20.0.9", **ts, **tailnet) == 200
    assert through("172.20.0.9", **ts, tailscale_user_login="me@example.com") == 401  # a script
    assert through("172.20.0.5", **ts, **tailnet) == 401  # not the sidecar


def test_compose_puts_the_front_door_in_front() -> None:
    base = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    svc = base["services"]
    assert "ports" not in svc["api"] and svc["wa-proxy"]["ports"] == ["${WA_API_PORT:-8010}:8000"]
    assert svc["api"]["environment"]["WA_FRONT_PROXY"] == "wa-proxy"
    assert svc["worker"]["stop_grace_period"] == "150s"
    up = svc["wa-updater"]
    assert up["profiles"] == ["updater"] and "ports" not in up and "/var/run/docker.sock:/var/run/docker.sock" in up["volumes"]
    serve = (ROOT / "deploy" / "tailscale" / "serve.json").read_text()
    assert "http://wa-proxy:8000" in serve and "http://api:8000" not in serve
    traefik = yaml.safe_load((ROOT / "docker-compose.traefik.yml").read_text())["services"]
    assert "wa-proxy" in traefik and "api" not in traefik
    caddy = (ROOT / "deploy" / "proxy" / "Caddyfile").read_text()
    assert "header_up X-WA-Peer {http.request.remote.host}" in caddy and "import /live/upstreams.caddy" in caddy
    assert "to api:8000" in (ROOT / "deploy" / "proxy" / "upstreams.default.caddy").read_text()
