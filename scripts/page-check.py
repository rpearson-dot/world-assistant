"""Open every page in a real browser, enter the token if asked, press Load, and fail on any script error.

Needs Playwright with Chromium (CI installs it). Reads WA_SMOKE_URL and WA_SMOKE_TOKEN.
"""

from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

PAGES = ("/settings", "/bus", "/trains", "/fuel", "/events", "/pollen", "/places", "/ha", "/graph")
# Console messages that are the browser's own noise about a failed request, not a script fault.
# A 4xx/5xx from a feature that is not set up (no API key) is reported by the smoke check instead.
IGNORE = ("Failed to load resource",)


def main() -> int:
    base = os.environ.get("WA_SMOKE_URL", "http://127.0.0.1:8000").rstrip("/")
    token = os.environ.get("WA_SMOKE_TOKEN", "")
    failures = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for path in PAGES:
            page = browser.new_page()
            problems: list[str] = []
            page.on("pageerror", lambda e, p=problems: p.append(f"script error: {e}"))
            page.on(
                "console",
                lambda m, p=problems: m.type == "error"
                and not any(i in m.text for i in IGNORE)
                and p.append(f"console error: {m.text}"),
            )
            resp = page.goto(base + path, wait_until="load")
            if resp is None or resp.status != 200:
                problems.append(f"HTTP {resp.status if resp else 'none'}")
            if page.locator("nav").count() == 0:
                problems.append("no navigation bar")
            if page.locator("#t").count():
                # From the local network the token box is hidden and the page loads by itself.
                if page.locator("#t").is_visible():
                    page.fill("#t", token)
                    if page.locator("#go").count():
                        page.click("#go")
                page.wait_for_timeout(1500)
            if page.locator("#t").count() and page.locator("main").count() == 0:
                problems.append("page body is empty")
            print(("FAIL " if problems else "ok   ") + path + ("  " + "; ".join(problems) if problems else ""))
            failures += bool(problems)
            page.close()
        browser.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
