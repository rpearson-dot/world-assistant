"""Look shared by the browser pages: one stylesheet and one nav bar. Static text only."""

from __future__ import annotations

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
  nav { display: flex; gap: 4px; flex-wrap: wrap; margin: 0 0 8px; }
  nav a { color: var(--dim); text-decoration: none; padding: 4px 10px; border-radius: 999px; font-size: 14px; font-weight: 600; }
  nav a:hover { background: var(--chip); } nav a.here { background: var(--chip); color: var(--ink); }
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

NAV = """<nav>
  <a href="/status" data-p="status">Status</a><a href="/bus" data-p="bus">Buses</a>
  <a href="/trains" data-p="trains">Trains</a><a href="/fuel" data-p="fuel">Fuel</a><a href="/energy" data-p="energy">Energy</a>
  <a href="/events" data-p="events">Events</a><a href="/pollen" data-p="pollen">Pollen</a><a href="/places" data-p="places">Places</a><a href="/map" data-p="map">Map</a>
</nav>
"""


def themed(page: str, here: str) -> str:
    """Fill the style and nav placeholders of a page."""
    nav = NAV.replace(f'data-p="{here}"', f'class="here" data-p="{here}"')
    return page.replace("/*BASE_CSS*/", BASE_CSS).replace("<!--NAV-->", nav)
