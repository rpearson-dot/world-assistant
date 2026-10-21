import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
dst.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 256, "height": 256})
    for f in sorted(src.glob("*.svg")):
        pg.set_content(f'<html><body style="margin:0;background:transparent">{f.read_text()}</body></html>')
        pg.screenshot(path=str(dst / f"{f.stem}.png"), omit_background=True, clip={"x": 0, "y": 0, "width": 256, "height": 256})
    # a contact sheet to look at them together
    cells = "".join(f'<div style="display:inline-block;margin:8px;background:#ddd;padding:6px">{f.read_text()}</div>' for f in sorted(src.glob("*.svg")))
    pg.set_viewport_size({"width": 1400, "height": 320})
    pg.set_content(f'<html><body style="margin:0;background:#ddd">{cells}</body></html>')
    pg.screenshot(path=str(dst / "_sheet.png"))
    b.close()
