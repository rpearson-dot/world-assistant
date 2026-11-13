"""Themes: every colour comes from app/themes.py, every theme is complete and readable."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Self

import pytest

from app import themes as T

APP = Path("app")
RAW = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{8}\b|['\"( :]#[0-9a-fA-F]{3}\b|\brgba?\(|\bhsla?\(")
HEX = re.compile(r"^#[0-9a-f]{6}$")


def _sources() -> list[Path]:
    return [p for p in APP.rglob("*.py") if p != APP / "themes.py"]


def test_no_page_writes_a_colour_of_its_own() -> None:
    found = [f"{p}:{n}: {line.strip()[:100]}" for p in _sources()
             for n, line in enumerate(p.read_text().splitlines(), 1) if RAW.search(line)]  # fmt: skip
    assert not found, "colours belong in app/themes.py; use a token:\n" + "\n".join(found)


def test_every_token_a_page_uses_is_defined() -> None:
    known = set(T.tokens("standard", "light")) | set(T.design("standard")) | set(T.FIXED)
    used: set[str] = set()
    for p in _sources():
        s = p.read_text()
        used |= set(re.findall(r"var\(--([a-z0-9-]+)", s))
        used |= set(re.findall(r"(?:tok|shade)\('([a-z0-9-]+)'\)", s))
        used |= set(re.findall(r"colour: '([a-z0-9-]+)'", s))  # the map's layers
    graph = (APP / "modules" / "graph" / "graph_page.py").read_text()
    used |= set(re.findall(r"[A-Za-z]+: '([a-z]+(?:-[0-9]+)?)'", graph[graph.index("const COLORS"):graph.index("const shade")]))
    local: set[str] = set()  # a page's own properties, set by its script (a tile's wear, --w, and its colour, --c)
    for p in _sources():
        local |= set(re.findall(r"setProperty\('--([a-z0-9-]+)'", p.read_text()))
    missing = sorted(u for u in used - known - local if not u.endswith('-'))  # 'series-' + n: built in the page
    assert not missing, missing


@pytest.mark.parametrize("theme", list(T.THEMES))
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_each_theme_is_complete_and_readable(theme: str, mode: str) -> None:
    t = T.tokens(theme, mode)
    assert set(t) == set(T.tokens("standard", mode))
    assert set(T.THEMES[theme][mode]) <= set(t), "a token Standard does not have"
    for k, v in t.items():
        if k in T.SURFACES:  # bevels, mixes, references to other tokens, blurs and gradients
            assert v == "none" or v.startswith(("var(--", "color-mix(", "inset ", "blur(", "radial-gradient(", "linear-gradient(")) or HEX.match(v), (k, v)
        else:
            assert HEX.match(v) or v.startswith("rgb("), (k, v)
    # the graph's colour picker takes #rrggbb: the colours it starts from must be that
    for k in [*(f"series-{n}" for n in range(1, 13)), "good", "warn", "bad", "severe", "dim"]:
        assert HEX.match(t[k]), (theme, mode, k)

    def lum(h: str) -> float:
        c = [int(h[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    def ratio(a: str, b: str) -> float:
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    for fg, bg, need in (("ink", "bg", 7), ("ink", "card", 7), ("dim", "card", 4.5), ("dim", "bg", 4.5),
                         ("on-accent", "accent", 4.5), ("good", "good-bg", 4.5), ("warn", "warn-bg", 4.5),
                         ("bad", "bad-bg", 4.5), ("accent", "card", 3), ("lv2-ink", "lv2", 4.5),
                         ("lv3-ink", "lv3", 4.5), ("lv4-ink", "lv4", 4.5), ("bar-ink", "bar", 7), ("bar-dim", "bar", 4.5),
                         ("bar-ink", "bar-here", 4.5), ("link", "card", 4.5), ("link-done", "card", 4.5), ("ink", "field", 7)):  # fmt: skip
        if not (HEX.match(t[fg]) and HEX.match(t[bg])):
            continue  # made from other tokens, checked as those
        assert ratio(t[fg], t[bg]) >= need, f"{theme} {mode}: {fg} on {bg} is {ratio(t[fg], t[bg]):.2f}, needs {need}"


def test_the_stylesheet_has_every_theme_and_pages_choose_before_drawing() -> None:
    from app.pagestyle import BASE_CSS, HEAD, themed

    for tid in T.THEMES:
        if tid != T.DEFAULT:
            assert f':root[data-theme="{tid}"]' in BASE_CSS and f':root[data-theme="{tid}"][data-dark]' in BASE_CSS
    assert ":root[data-dark]" in BASE_CSS and "prefers-color-scheme: dark" in BASE_CSS
    assert T.HEAD_SCRIPT in HEAD
    page = themed("<html><head><style>/*BASE_CSS*/</style></head><body><!--NAV--></body></html>", "map")
    assert page.index("waTheme") < page.index("<body")


def test_the_look_tab_lists_the_themes(tmp_path: Path) -> None:
    import json
    import subprocess

    from fastapi.testclient import TestClient

    from app.main import app

    r = TestClient(app).get("/settings")
    assert r.status_code == 200 and 'data-tab="look"' in r.text
    for t in T.THEMES.values():
        assert json.dumps(t["name"]) in r.text
    for script in re.findall(r"<script>(.*?)</script\s*>", r.text, re.DOTALL | re.IGNORECASE):
        f = tmp_path / "s.js"
        f.write_text(script)
        subprocess.run(["node", "--check", str(f)], check=True)


def test_the_servers_default_goes_into_every_page_until_a_device_chooses(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app import look
    from app.main import app
    from app.pagestyle import themed

    page = "<html><head><style>/*BASE_CSS*/</style></head><body><!--NAV--></body></html>"
    assert "D=['standard','auto']" in themed(page, "map")
    monkeypatch.setitem(T.SERVER, "theme", "glass")
    monkeypatch.setitem(T.SERVER, "mode", "dark")
    assert "D=['glass','dark']" in themed(page, "map") and "D=['standard','auto']" not in themed(page, "map")
    assert "D=['standard','auto']" in T.head_script("nope", "nope")  # never anything but a known theme and mode

    saved: dict[str, str] = {}

    class Conn:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *a: object) -> None:
            return None

        async def execute(self, stmt: object, params: dict[str, str]) -> None:
            saved.update(params)

    class Engine:
        def begin(self) -> Conn:
            return Conn()

    monkeypatch.setenv("API_TOKEN", "s3cret")
    monkeypatch.setattr(app.state, "engine", Engine(), raising=False)
    c = TestClient(app)
    h = {"Authorization": "Bearer s3cret"}
    assert c.put("/v1/settings/look", json={"theme": "teal", "mode": "auto"}, headers=h).status_code == 422
    r = c.put("/v1/settings/look", json={"theme": "win95", "mode": "light"}, headers=h)
    assert r.json() == {"theme": "win95", "mode": "light"} and T.SERVER["theme"] == "win95" and "win95" in saved["v"]
    assert c.get("/v1/settings/look", headers=h).json()["theme"] == "win95"
    assert look.KEY == saved["k"]
