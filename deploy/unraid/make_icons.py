"""Draws the World Assistant icons as SVG: a green earth with a USB cable hanging off it, plus a
small badge per container showing its role. All artwork here is original."""
import sys
from pathlib import Path

BG = "#14231a"  # deep green-black tile, rounded like Home Assistant's
LAND = "#4cc15a"

GLOBE = f"""
  <defs><clipPath id="ball"><circle cx="128" cy="108" r="74"/></clipPath></defs>
  <circle cx="128" cy="108" r="74" fill="#1f3b2a"/>
  <g clip-path="url(#ball)" fill="{LAND}">
    <path d="M70 62c14-14 38-18 52-8 6 5 2 12-6 16-9 5-6 14-16 18-11 5-18 14-16 26 2 9-4 14-12 8-14-11-22-38-2-60z"/>
    <path d="M142 118c10-8 26-6 36 4 10 11 14 28 6 42-8 13-22 14-26 4-3-8-12-10-14-20-2-11-6-22-2-30z"/>
    <path d="M150 46c10-6 24-4 32 6 5 6 0 12-8 12-8 0-10 8-18 6-9-2-14-18-6-24z"/>
  </g>
  <circle cx="128" cy="108" r="74" fill="none" stroke="{LAND}" stroke-width="10"/>
"""

# a USB cable leaving the bottom of the globe and dangling in a loose S, ending in a USB-A plug
CABLE = """
  <path d="M128 186 C128 212 96 214 100 232 C103 244 126 240 138 232" fill="none" stroke="#0b140f" stroke-width="12" stroke-linecap="round"/>
  <path d="M128 186 C128 212 96 214 100 232 C103 244 126 240 138 232" fill="none" stroke="#e9eef2" stroke-width="7" stroke-linecap="round"/>
  <g transform="translate(140 233) rotate(-30)">
    <rect x="-3" y="-11" width="30" height="22" rx="3" fill="#0b140f"/>
    <rect x="-1" y="-9" width="26" height="18" rx="2" fill="#c9d3da"/>
    <rect x="22" y="-8" width="10" height="16" rx="1" fill="#0b140f"/>
    <rect x="24" y="-5" width="6" height="3" fill="#e9eef2"/>
    <rect x="24" y="2" width="6" height="3" fill="#e9eef2"/>
  </g>
"""

BADGES = {
    "world-assistant": "",
    "worker": """
      <g fill="#fff"><circle cx="0" cy="0" r="14"/>
      {teeth}
      </g><circle cx="0" cy="0" r="6" fill="#e07b00"/>""",
    "database": """
      <g fill="#fff"><ellipse cx="0" cy="-11" rx="15" ry="6"/>
      <path d="M-15 -11v22c0 3.3 6.7 6 15 6s15-2.7 15-6v-22c0 3.3-6.7 6-15 6s-15-2.7-15-6z"/></g>
      <path d="M-15 -1c0 3.3 6.7 6 15 6s15-2.7 15-6M-15 9c0 3.3 6.7 6 15 6s15-2.7 15-6" fill="none" stroke="#7a3fb0" stroke-width="2.4"/>""",
    "cache": """
      <path d="M4 -19 -11 3h10l-4 17 16-23H1z" fill="#fff" stroke="#fff" stroke-width="2" stroke-linejoin="round"/>""",
    "routing": """
      <circle cx="-12" cy="12" r="5.5" fill="#fff"/><circle cx="12" cy="-12" r="5.5" fill="#fff"/>
      <path d="M-12 12C-12 -2 12 8 12 -12" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-dasharray="1 9"/>
      <path d="M-12 12C-12 -2 12 8 12 -12" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-dasharray="7 7"/>""",
}
COLOURS = {"worker": "#f08c00", "database": "#8e4ec6", "cache": "#d6453d", "routing": "#12a594"}


def teeth() -> str:

    out = []
    for i in range(8):
        a = i * 45
        out.append(f'<rect x="-3.5" y="-20" width="7" height="8" rx="1.5" transform="rotate({a})"/>')
    return "".join(out)


def svg(name: str) -> str:
    badge = ""
    if name != "world-assistant":
        inner = BADGES[name].replace("{teeth}", teeth())
        badge = (f'<g transform="translate(196 196)"><circle r="40" fill="{BG}"/>'
                 f'<circle r="34" fill="{COLOURS[name]}"/>{inner}</g>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">'
            f'<rect width="256" height="256" rx="52" fill="{BG}"/>{GLOBE}{CABLE}{badge}</svg>')


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for n in BADGES:
        (out / f"{n}.svg").write_text(svg(n), encoding="utf-8")
    print(*sorted(p.name for p in out.glob("*.svg")))
