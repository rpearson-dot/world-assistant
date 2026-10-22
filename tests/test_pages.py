import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.mark.parametrize(
    "path", ["/settings", "/bus", "/trains", "/fuel", "/places", "/events", "/pollen", "/energy", "/ha", "/graph"]
)
def test_every_page_is_themed_and_has_nav(path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert "/*BASE_CSS*/" not in r.text and "<!--NAV-->" not in r.text
    assert "--accent" in r.text and '<nav class="tabs"' in r.text
    assert 1 <= r.text.count('class="here"') <= 2  # in the tab bar, and in the More sheet on phones
    assert 'rel="manifest"' in r.text and "viewport-fit=cover" in r.text
    assert "http://" not in r.text and "https://" not in r.text  # no external resources


def test_the_old_status_page_goes_to_settings() -> None:
    r = client.get("/status", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/settings#system"


def test_the_app_can_be_installed() -> None:
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.headers["content-type"].startswith("application/manifest+json")
    d = m.json()
    assert d["display"] == "standalone" and {i["sizes"] for i in d["icons"]} == {"192x192", "512x512"}
    for icon in d["icons"]:
        assert client.get(icon["src"]).status_code == 200
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"] and "/v1/" in sw.text  # never caches the API
    assert client.get("/", follow_redirects=False).status_code == 307
