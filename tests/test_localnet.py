"""Local requests need no token; everything else still does (app/localnet.py)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import require_token
from app.config import get_settings
from app.localnet import PAGE_SNIPPET, home_host, host_name

LAN = ("192.168.1.20", 50000)


@pytest.fixture(autouse=True)
def settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("API_TOKEN", "tok")
    monkeypatch.delenv("WA_LOCAL_NO_TOKEN", raising=False)
    monkeypatch.delenv("WA_LOCAL_HOSTS", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def api() -> FastAPI:
    app = FastAPI()

    @app.get("/v1/thing", dependencies=[Depends(require_token)])
    def read() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/v1/thing", dependencies=[Depends(require_token)])
    def change() -> dict[str, bool]:
        return {"ok": True}

    return app


def get(client: tuple[str, int] = LAN, host: str = "192.168.1.10:8010", **headers: str) -> int:
    c = TestClient(api(), client=client, base_url=f"http://{host}")
    return c.get("/v1/thing", headers={k.replace("_", "-"): v for k, v in headers.items()}).status_code


def post(origin: str | None = None, fetch_site: str | None = None, host: str = "192.168.1.10:8010") -> int:
    headers = {}
    if origin:
        headers["Origin"] = origin
    if fetch_site:
        headers["Sec-Fetch-Site"] = fetch_site
    return TestClient(api(), client=LAN, base_url=f"http://{host}").post("/v1/thing", headers=headers).status_code


def test_the_home_network_needs_no_token() -> None:
    assert get() == 200
    assert get(client=("10.0.0.5", 1)) == 200 and get(client=("172.18.0.1", 1)) == 200  # Docker's gateway too
    assert get(client=("127.0.0.1", 1), host="localhost:8010") == 200
    assert get(client=("::1", 1), host="[::1]:8010") == 200
    assert get(client=("100.101.2.3", 1), host="nas.tail1234.ts.net") == 200  # Tailscale
    assert get(client=("::ffff:192.168.1.20", 1)) == 200


def test_the_internet_still_needs_the_token() -> None:
    assert get(client=("81.2.69.160", 1)) == 401
    assert get(client=("81.2.69.160", 1), authorization="Bearer tok") == 200
    assert get(client=("2a00:1450::1", 1), host="[2a00:1450::1]") == 401


def test_anything_through_a_proxy_is_not_local() -> None:
    """A reverse proxy (Traefik) connects from a private address but may be relaying the internet."""
    for header in ("x_forwarded_for", "x_real_ip", "forwarded", "x_forwarded_host"):
        assert get(**{header: "81.2.69.160"}) == 401
    assert get(x_forwarded_for="81.2.69.160", authorization="Bearer tok") == 200


def test_names_only_a_home_network_uses() -> None:
    """Another site's name re-pointed at this server (DNS rebinding) must not count as local."""
    for host in ("192.168.1.10", "nas", "nas.lan", "nas.local", "server.home.arpa", "wa.internal", "localhost"):
        assert get(host=host) == 200, host
    for host in ("evil.example.com", "192.168.1.10.nip.io", "attacker.co.uk"):
        assert get(host=host) == 401, host


def test_extra_home_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_LOCAL_HOSTS", "wa.example.org, other.example.net")
    get_settings.cache_clear()
    assert get(host="wa.example.org:8010") == 200 and get(host="third.example.net") == 401


def test_another_site_cannot_change_anything() -> None:
    assert post() == 200  # curl, a script, Home Assistant: no Origin
    assert post(origin="http://192.168.1.10:8010", fetch_site="same-origin") == 200  # this server's own page
    assert post(origin="https://evil.example.com") == 401
    assert post(fetch_site="cross-site") == 401
    assert post(origin="http://192.168.1.11:8010") == 401  # another device's page on the LAN


def test_it_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WA_LOCAL_NO_TOKEN", "false")
    get_settings.cache_clear()
    assert get() == 401 and get(authorization="Bearer tok") == 200


def test_host_names() -> None:
    assert host_name("NAS.lan:8010") == "nas.lan" and host_name("[fe80::1]:8010") == "fe80::1"
    assert host_name("fe80::1") == "fe80::1" and home_host("") is False


def test_pages_hide_the_token_box_only_for_local_browsers() -> None:
    from app.main import app

    local = TestClient(app, client=LAN, base_url="http://192.168.1.10:8010").get("/status").text
    outside = TestClient(app, client=("81.2.69.160", 1)).get("/status").text
    assert PAGE_SNIPPET in local and PAGE_SNIPPET not in outside
    assert 'id="t"' in local and 'id="t"' in outside  # the box is still there, hidden, for a real token


# --- Tailscale sidecar ---------------------------------------------------------------------------------

SIDECAR = ("172.20.0.9", 40000)
VIA = {"x-forwarded-for": "100.101.2.3", "x-forwarded-proto": "https", "tailscale-user-login": "me@example.com"}


@pytest.fixture
def tailnet(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import localnet

    monkeypatch.setenv("WA_TAILSCALE_PROXY", "wa-tailscale")
    get_settings.cache_clear()
    monkeypatch.setattr(localnet, "_proxy_ips", lambda name: frozenset({"172.20.0.9"}) if name == "wa-tailscale" else frozenset())


def via_tailnet(method: str = "GET", **headers: str) -> int:
    c = TestClient(api(), client=SIDECAR, base_url="http://world-assistant.tail1234.ts.net")
    return c.request(method, "/v1/thing", headers={**VIA, **{k.replace("_", "-"): v for k, v in headers.items()}}).status_code


def test_the_web_pages_work_from_the_tailnet_but_the_api_needs_the_token(tailnet: None) -> None:
    assert via_tailnet(sec_fetch_site="same-origin") == 200  # the page's own fetch, in a browser
    assert via_tailnet("POST", sec_fetch_site="same-origin", origin="https://world-assistant.tail1234.ts.net") == 200
    assert via_tailnet() == 401  # Siri Shortcuts, MCP clients, curl: no Sec-Fetch-Site
    assert via_tailnet(sec_fetch_site="cross-site") == 401
    assert via_tailnet(authorization="Bearer tok") == 200


def test_only_the_sidecar_and_a_signed_in_user_count(tailnet: None) -> None:
    other = TestClient(api(), client=("172.20.0.5", 1), base_url="http://world-assistant.tail1234.ts.net")
    assert other.get("/v1/thing", headers={**VIA, "sec-fetch-site": "same-origin"}).status_code == 401
    no_user = {k: v for k, v in VIA.items() if k != "tailscale-user-login"}
    c = TestClient(api(), client=SIDECAR, base_url="http://world-assistant.tail1234.ts.net")
    assert c.get("/v1/thing", headers={**no_user, "sec-fetch-site": "same-origin"}).status_code == 401


def test_without_the_sidecar_setting_the_tailnet_is_the_internet(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import localnet

    monkeypatch.setattr(localnet, "_proxy_ips", lambda name: frozenset({"172.20.0.9"}))
    assert via_tailnet(sec_fetch_site="same-origin") == 401


def test_tailnet_pages_hide_the_token_box(tailnet: None) -> None:
    from app.main import app

    page = TestClient(app, client=SIDECAR, base_url="https://world-assistant.tail1234.ts.net").get("/status", headers=VIA).text
    assert PAGE_SNIPPET in page
