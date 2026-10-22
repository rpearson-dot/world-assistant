"""Draws the World Assistant icons as SVG: a green earth with a USB cable hanging off it and, for the
supporting containers, a coloured band along the bottom naming the container's role. All artwork here
is original, and the band uses plain role words rather than any product's name or logo."""
import sys
from pathlib import Path

BG = "#14231a"  # deep green-black tile, rounded like Home Assistant's
LAND = "#4cc15a"
SEA = "#1f3b2a"

ROLES = {  # file name: (band text, band colour); the api icon has no band
    "world-assistant": None,
    "worker": ("WORKER", "#f08c00"),
    "database": ("DATABASE", "#8e4ec6"),
    "cache": ("CACHE", "#d6453d"),
    "routing": ("ROUTING", "#12a594"),
    "vault": ("VAULT", "#c79a00"),
    "graph": ("GRAPH", "#2d7ff9"),
}

GLOBE = f"""
  <defs><clipPath id="ball"><circle cx="128" cy="92" r="66"/></clipPath></defs>
  <circle cx="128" cy="92" r="66" fill="{SEA}"/>
  <g clip-path="url(#ball)" fill="{LAND}" transform="translate(128 92) scale(.86) translate(-128 -108)">
    <path d="M70 62c14-14 38-18 52-8 6 5 2 12-6 16-9 5-6 14-16 18-11 5-18 14-16 26 2 9-4 14-12 8-14-11-22-38-2-60z"/>
    <path d="M142 118c10-8 26-6 36 4 10 11 14 28 6 42-8 13-22 14-26 4-3-8-12-10-14-20-2-11-6-22-2-30z"/>
    <path d="M150 46c10-6 24-4 32 6 5 6 0 12-8 12-8 0-10 8-18 6-9-2-14-18-6-24z"/>
  </g>
  <circle cx="128" cy="92" r="66" fill="none" stroke="{LAND}" stroke-width="9"/>
"""

# a data cable (not a power lead) hanging from the globe in the same green, ending in a USB-A plug
# pointing down: a metal shell with the hollow, tongued opening that makes it read as USB
CABLE = f"""
  <path d="M112 154 C98 172 76 166 80 184 C83 197 100 196 114 192" fill="none" stroke="{LAND}" stroke-width="7" stroke-linecap="round"/>
  <rect x="110" y="185" width="13" height="13" rx="3.5" fill="{LAND}"/>
  <rect x="121" y="182" width="38" height="19" rx="2.5" fill="{LAND}"/>
  <rect x="125" y="186" width="30" height="11" rx="1.5" fill="{BG}"/>
  <rect x="125" y="191.5" width="30" height="5.5" fill="{LAND}"/>
  <rect x="132" y="187.5" width="4.5" height="3" fill="{LAND}"/>
  <rect x="143.5" y="187.5" width="4.5" height="3" fill="{LAND}"/>
"""


def band(text: str, colour: str) -> str:
    size = 25 if len(text) <= 6 else 22
    return (f'<rect x="24" y="212" width="208" height="34" rx="17" fill="{colour}"/>'
            f'<text x="128" y="236" text-anchor="middle" font-family="DejaVu Sans, Arial, Helvetica, sans-serif" '
            f'font-weight="800" font-size="{size}" letter-spacing="2" fill="#ffffff">{text}</text>')


def svg(name: str) -> str:
    role = ROLES[name]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">'
            f'<rect width="256" height="256" rx="52" fill="{BG}"/>{GLOBE}{CABLE}'
            f'{band(*role) if role else ""}</svg>')


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for n in ROLES:
        (out / f"{n}.svg").write_text(svg(n), encoding="utf-8")
    print(*sorted(p.name for p in out.glob("*.svg")))
