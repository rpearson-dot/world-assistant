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
    sub = r.text.count('<nav class="subtabs"')  # a section's own tabs (Shopping, Environment) mark theirs too
    assert 1 <= r.text.count('class="here"') - sub <= 2  # in the tab bar, and in the More sheet on phones
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


def test_every_page_script_parses(tmp_path: "Path") -> None:  # noqa: F821
    """Each page's scripts, as served, must parse: one bad line (a name declared twice) leaves the whole page dead,
    as the map was in 0.40.0 to 0.42.0."""
    import re
    import subprocess

    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    from pathlib import Path

    found = {m for f in Path("app").rglob("*.py") for m in re.findall(r'page_router\.get\("(/[^"{]*)"', f.read_text())}
    pages = sorted(found)
    checked = 0
    for path in pages:
        r = c.get(path)
        if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
            continue
        for i, js in enumerate(re.findall(r"<script>(.*?)</script\s*>", r.text, re.DOTALL | re.IGNORECASE)):
            f = tmp_path / f"{path.strip('/').replace('/', '_') or 'root'}_{i}.js"
            f.write_text(js)
            p = subprocess.run(["node", "--check", str(f)], check=False, capture_output=True, text=True)
            why = next((x for x in p.stderr.splitlines() if "Error" in x), p.stderr.strip()[:200])
            assert p.returncode == 0, f"{path} script {i}: {why}"
        checked += 1
    assert checked >= 10 and "/map" in pages
