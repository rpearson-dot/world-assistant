"""Look shared by the browser pages: one stylesheet and one nav bar. Static text only."""

from __future__ import annotations

from starlette.requests import HTTPConnection

from app.localnet import PAGE_SNIPPET, page_without_token

BASE_CSS = """
  :root {
    color-scheme: light dark;
    --bg: #f5f6f8; --card: #ffffff; --ink: #1c2330; --dim: #667085; --line: #e3e6ec;
    --accent: #1f6feb; --good: #1a7f4b; --good-bg: #e6f4ec; --warn: #b54708; --warn-bg: #fdf0e1;
    --bad: #b42318; --bad-bg: #fde8e6; --chip: #eef1f6;
  }
  @media (prefers-color-scheme: dark) { :root {
    --bg: #12161d; --card: #1b212b; --ink: #e8ecf3; --dim: #98a2b3; --line: #2b3340;
    --accent: #6ea8fe; --good: #5fd39a; --good-bg: #17352a; --warn: #f4b26a; --warn-bg: #3a2a14;
    --bad: #ff8b82; --bad-bg: #3d1c19; --chip: #252d3a; } }
  [hidden] { display: none !important; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 960px; margin: 0 auto; padding: 16px; }
  h1 { font-size: 22px; margin: 4px 0 12px; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: var(--dim); margin: 0 0 10px; font-weight: 600; }
  .bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
  input, select, button, textarea { font: inherit; color: inherit; background: var(--card); border: 1px solid var(--line);
    border-radius: 8px; padding: 8px 10px; }
  input[type=password] { flex: 1 1 220px; min-width: 0; }
  button { cursor: pointer; } button.primary { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
  button.link { border: 0; background: none; color: var(--accent); padding: 0 4px; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px; margin-bottom: 14px; }
  .hero { display: flex; gap: 14px; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; }
  .hero .say { font-size: 20px; font-weight: 650; line-height: 1.3; flex: 1 1 320px; }
  .pill { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 13px; font-weight: 600; background: var(--chip); color: var(--dim); }
  .pill.on { background: var(--good-bg); color: var(--good); }
  .grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
  .next { display: flex; flex-wrap: wrap; gap: 10px; }
  .stop { flex: 1 1 130px; background: var(--chip); border-radius: 12px; padding: 10px 12px; }
  .stop .name { font-size: 13px; color: var(--dim); } .stop .mins { font-size: 26px; font-weight: 700; line-height: 1.15; }
  .stop .more { font-size: 13px; color: var(--dim); }
  table { width: 100%; border-collapse: collapse; } th { text-align: left; font-size: 12px; color: var(--dim); font-weight: 600; }
  th, td { padding: 6px 6px; border-bottom: 1px solid var(--line); } tr:last-child td { border-bottom: 0; }
  .dim { color: var(--dim); font-size: 13px; } .err { color: var(--bad); } .ok { color: var(--good); font-weight: 600; }
  .badge { padding: 1px 8px; border-radius: 6px; font-size: 12px; font-weight: 600; }
  .badge.good { background: var(--good-bg); color: var(--good); } .badge.warn { background: var(--warn-bg); color: var(--warn); }
  .badge.bad { background: var(--bad-bg); color: var(--bad); }
  .meter { height: 8px; border-radius: 4px; background: var(--chip); overflow: hidden; min-width: 80px; }
  .meter > span { display: block; height: 100%; background: var(--good); }
  .meter.low > span { background: var(--warn); } .meter.bad > span { background: var(--bad); }
  .form { display: grid; gap: 12px; } .form label { display: grid; gap: 4px; font-weight: 600; font-size: 14px; }
  .form label span.hint { font-weight: 400; color: var(--dim); font-size: 13px; }
  .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; } .row > * { flex: 0 1 auto; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; } .chip { background: var(--chip); border-radius: 999px; padding: 2px 6px 2px 12px; font-size: 14px; }
  textarea { width: 100%; font: 13px/1.4 ui-monospace, monospace; min-height: 160px; }
  details > summary { cursor: pointer; font-weight: 600; } .hidden { display: none; }
  /* app shell: a top bar with every page on wide screens; a bottom tab bar and a More sheet on phones */
  .appbar { position: sticky; top: 0; z-index: 20; display: flex; align-items: center; gap: 12px; margin: -16px -16px 12px;
    padding: calc(8px + env(safe-area-inset-top)) 16px 8px; background: color-mix(in srgb, var(--bg) 88%, transparent);
    backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); border-bottom: 1px solid var(--line); }
  .brand { display: flex; align-items: center; gap: 8px; color: var(--ink); text-decoration: none; font-weight: 700; white-space: nowrap; }
  .brand img { border-radius: 7px; }
  nav.tabs { display: flex; gap: 2px; flex-wrap: wrap; flex: 1; }
  nav.tabs a, nav.tabs button { display: flex; align-items: center; gap: 6px; color: var(--dim); text-decoration: none; padding: 5px 10px;
    border-radius: 999px; font-size: 14px; font-weight: 600; border: 0; background: none; }
  nav.tabs svg { width: 18px; height: 18px; }
  nav.tabs a:hover { background: var(--chip); } nav.tabs a.here { background: var(--chip); color: var(--ink); }
  nav.tabs .more { display: none; }
  .sheet { position: fixed; inset: 0; z-index: 30; background: rgb(0 0 0 / .35); display: flex; align-items: flex-end; }
  .sheet-grid { width: 100%; background: var(--card); border-radius: 18px 18px 0 0; padding: 14px 12px calc(84px + env(safe-area-inset-bottom));
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
  .sheet-grid a { display: grid; justify-items: center; gap: 4px; padding: 12px 4px; border-radius: 12px; color: var(--ink);
    text-decoration: none; font-size: 13px; font-weight: 600; text-align: center; }
  .sheet-grid a.here, .sheet-grid a:active { background: var(--chip); }
  @media (max-width: 760px) {
    body.app main { padding-bottom: calc(84px + env(safe-area-inset-bottom)); }
    /* a backdrop filter would make the header the box the fixed tab bar is placed in: keep it plain here */
    .appbar { justify-content: center; backdrop-filter: none; -webkit-backdrop-filter: none; background: var(--bg); }
    .brand span { font-size: 16px; }
    nav.tabs { position: fixed; left: 0; right: 0; bottom: 0; z-index: 40; flex-wrap: nowrap; justify-content: space-around; gap: 0;
      background: var(--card); border-top: 1px solid var(--line); padding: 6px 4px calc(6px + env(safe-area-inset-bottom)); }
    nav.tabs a, nav.tabs .more { flex: 1; flex-direction: column; gap: 2px; padding: 4px 2px; border-radius: 10px; font-size: 11px; min-width: 0; }
    nav.tabs svg { width: 22px; height: 22px; }
    nav.tabs a:not([data-main]) { display: none; }
    nav.tabs .more { display: flex; align-items: center; }
    nav.tabs a.here { background: none; color: var(--accent); }
    h1 { font-size: 20px; }
    .card { padding: 14px; border-radius: 12px; }
    input, select, button, textarea { font-size: 16px; }  /* no zoom-on-focus on iPhone */
    th, td { padding: 6px 4px; }
  }
  @media (min-width: 761px) { .sheet { display: none !important; } }
  .muted { color: var(--dim); } .late { color: var(--bad); font-weight: 600; }
  h3 { font-size: 14px; margin: 14px 0 4px; color: var(--dim); }
  .card h2 .push { float: right; } .scroll { overflow-x: auto; }
  .row label, .fields label { display: grid; gap: 2px; font-size: 13px; color: var(--dim); font-weight: 600; }
  .checks label { display: inline-flex; flex-direction: row; align-items: center; gap: 5px; margin: 3px 14px 3px 0;
    color: var(--ink); font-weight: 400; font-size: 14px; }
  .checks input { padding: 0; }
  .best { border-color: var(--good); background: var(--good-bg); } .best h2 { color: var(--good); }
  .cmp p { margin: 3px 0; } .cmp .big { font-weight: 650; }
  form.row { margin: 0 0 8px; }
  td button { padding: 3px 10px; font-size: 13px; }
  pre { background: var(--chip); padding: 12px; overflow-x: auto; white-space: pre-wrap; word-break: break-word; border-radius: 8px; }
"""

# Every page, in the order the navigation shows them. "main" ones are on the phone's bottom tab bar; the rest
# are behind More. Icons are simple 24px line drawings, drawn here (no icon font, nothing from outside).
ICONS = {
    "bus": '<rect x="4" y="3" width="16" height="15" rx="3"/><path d="M4 11h16M8 21v-3M16 21v-3"/><circle cx="8" cy="14.5" r="1"/><circle cx="16" cy="14.5" r="1"/>',
    "train": '<rect x="5" y="3" width="14" height="14" rx="4"/><path d="M5 10h14M8 21l2-4M16 21l-2-4"/><circle cx="9" cy="13.5" r="1"/><circle cx="15" cy="13.5" r="1"/>',
    "map": '<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2z"/><path d="M9 4v14M15 6v14"/>',
    "fuel": '<path d="M5 21V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v16M3 21h14M5 10h10"/><path d="M15 8h2l2 2v7a1.5 1.5 0 0 1-3 0v-3h-1"/>',
    "energy": '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8z"/>',
    "events": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "pollen": '<circle cx="12" cy="12" r="3"/><path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.9 2.9M15.5 15.5l2.9 2.9M5.6 18.4l2.9-2.9M15.5 8.5l2.9-2.9"/>',
    "places": '<path d="M12 21s-7-6.1-7-11.5A7 7 0 0 1 19 9.5C19 14.9 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    "graph": '<circle cx="5" cy="6" r="2"/><circle cx="19" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><circle cx="12" cy="10" r="2"/><path d="M6.7 7l3.6 2M17.3 7l-3.6 2M12 12v4"/>',
    "ha": '<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-5h4v5"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "more": '<circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/>',
}  # fmt: skip

PAGES = (  # (id, path, label, icon, on the phone's tab bar)
    ("bus", "/bus", "Buses", "bus", True),
    ("trains", "/trains", "Trains", "train", True),
    ("map", "/map", "Map", "map", True),
    ("fuel", "/fuel", "Fuel", "fuel", True),
    ("energy", "/energy", "Energy", "energy", False),
    ("events", "/events", "Events", "events", False),
    ("pollen", "/pollen", "Pollen", "pollen", False),
    ("places", "/places", "Places", "places", False),
    ("graph", "/graph", "Graph", "graph", False),
    ("ha", "/ha", "Home Assistant", "ha", False),
    ("settings", "/settings", "Settings", "settings", False),
)


def _icon(name: str) -> str:
    return (f'<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')  # fmt: skip


def _link(page: tuple[str, str, str, str, bool]) -> str:
    pid, path, label, icon, main = page
    return f'<a href="{path}" data-p="{pid}"{" data-main" if main else ""}>{_icon(icon)}<span>{label}</span></a>'


NAV = (
    '<header class="appbar"><a class="brand" href="/" aria-label="World Assistant">'
    '<img src="/static/app/icon-192.png" alt="" width="28" height="28"><span>World Assistant</span></a>'
    '<nav class="tabs" aria-label="Pages">' + "".join(_link(p) for p in PAGES)
    + f'<button type="button" class="more" aria-expanded="false" aria-controls="sheet">{_icon("more")}<span>More</span>'
    "</button></nav></header>"
    '<div id="sheet" class="sheet" hidden><div class="sheet-grid">'
    + "".join(_link(p) for p in PAGES if not p[4])
    + "</div></div>"
    "<script>(()=>{const b=document.querySelector('.tabs .more'),s=document.getElementById('sheet');"
    "b.onclick=()=>{const o=s.hidden;s.hidden=!o;b.setAttribute('aria-expanded',o)};"
    "s.onclick=e=>{if(e.target===s){s.hidden=true;b.setAttribute('aria-expanded','false')}};"
    "if('serviceWorker' in navigator&&isSecureContext)navigator.serviceWorker.register('/sw.js').catch(()=>{});})()</script>"
)

HEAD = (
    '<link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#14231a">'
    '<link rel="icon" href="/static/app/icon-192.png"><link rel="apple-touch-icon" href="/static/app/apple-touch-icon.png">'
    '<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
    '<meta name="apple-mobile-web-app-title" content="World Assistant">'
)


def themed(page: str, here: str, request: HTTPConnection | None = None) -> str:
    """Fill the style, head and nav placeholders of a page. From the local network, the token box goes away."""
    nav = NAV.replace(f'data-p="{here}"', f'class="here" aria-current="page" data-p="{here}"')
    if request is not None and page_without_token(request):
        nav += PAGE_SNIPPET
    page = page.replace("</head>", HEAD + "</head>", 1).replace("<body>", '<body class="app">', 1)
    page = page.replace('content="width=device-width, initial-scale=1"', 'content="width=device-width, initial-scale=1, viewport-fit=cover"')
    page = page.replace('content="width=device-width,initial-scale=1"', 'content="width=device-width, initial-scale=1, viewport-fit=cover"')
    return page.replace("/*BASE_CSS*/", BASE_CSS).replace("<!--NAV-->", nav)
