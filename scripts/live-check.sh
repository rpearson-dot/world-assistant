#!/usr/bin/env bash
# Live check: ask every outside source World Assistant uses, and its own endpoints for home, and write what comes
# back to one text file to read or send on. Read-only (apart from the Wishlist and Outdoors checks, which record what
# they find, as their morning runs do). Uses about two of Data Thistle's monthly requests.
#
#     bash scripts/live-check.sh                 # writes live-check-<date>-<time>.txt here
#     bash scripts/live-check.sh --quick         # skips the slower shop and timetable checks
#     bash scripts/live-check.sh --out DIR       # writes it in DIR instead, keeping the latest ten there
#
# scripts/deploy.sh runs it after every update, in the background, into WA_DATA_DIR/live-checks (WA_LIVE_CHECK=0 turns
# that off). Its result never decides whether an update is kept: other people's services are down sometimes.
#
# Runs inside the api container (docker compose exec), so it needs nothing else installed. No key, token or password
# is printed: the API token is read inside the container and only sent to the API itself.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
QUICK=0
DIR="."
while [ $# -gt 0 ]; do
  case "$1" in
    --quick) QUICK=1 ;;
    --out) DIR="${2:?--out needs a folder}"; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
mkdir -p "$DIR"
OUT="$DIR/live-check-$(date +%Y%m%d-%H%M).txt"
echo "Writing $OUT (this takes a few minutes)..."

docker compose exec -T -e QUICK="$QUICK" api python - <<'PY' >"$OUT" 2>&1
import json, os, subprocess, sys, time
from datetime import datetime

import httpx
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.version import VERSION

QUICK = os.environ.get("QUICK") == "1"
s = get_settings()
H = {"Authorization": f"Bearer {s.api_token}"}
API = "http://localhost:8000"
results: list[tuple[str, str]] = []


def head(t: str) -> None:
    print("\n" + "=" * 100 + f"\n== {t}\n" + "=" * 100, flush=True)


def note(name: str, ok: bool, why: str = "") -> None:
    results.append((name, ("OK   " if ok else "FAIL ") + why))


def run(name: str, *args: str, timeout: int = 180) -> None:
    head(f"{name}:  python -m {' '.join(args)}")
    t = time.time()
    try:
        p = subprocess.run([sys.executable, "-m", *args], capture_output=True, text=True, timeout=timeout)
        out = (p.stdout + p.stderr).strip()
        print(out[:12000] + ("\n... (cut)" if len(out) > 12000 else ""))
        failed = p.returncode != 0 or "FAILED" in out or "Traceback" in out
        note(name, not failed, f"{time.time() - t:.0f}s" + (" (see FAILED or Traceback above)" if failed else ""))
    except subprocess.TimeoutExpired:
        print(f"timed out after {timeout}s")
        note(name, False, f"timed out after {timeout}s")


def get(name: str, path: str, show: int = 4000, **params: object) -> object:
    head(f"{name}:  GET {path} {params or ''}")
    t = time.time()
    try:
        r = httpx.get(API + path, params={k: v for k, v in params.items() if v is not None}, headers=H, timeout=120)
        body = r.json() if "json" in r.headers.get("content-type", "") else r.text
        print(f"HTTP {r.status_code} in {time.time() - t:.1f}s")
        print(json.dumps(body, indent=1, default=str)[:show] if not isinstance(body, str) else body[:show])
        note(name, r.status_code < 400, f"HTTP {r.status_code}, {time.time() - t:.1f}s")
        return body
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {type(exc).__name__}: {exc}")
        note(name, False, type(exc).__name__)
        return None


head(f"World Assistant {VERSION} live check, {datetime.now():%A %d %B %Y %H:%M}")
eng = create_engine(s.database_url)  # psycopg, which works without asyncio too
with eng.connect() as c:
    home = c.execute(text("SELECT id, name, lat, lon FROM place ORDER BY (kind = 'home') DESC, (id = 'home') DESC, name LIMIT 1")).first()
    places = c.execute(text("SELECT count(*) FROM place")).scalar()
lat, lon = (float(home.lat), float(home.lon)) if home else (51.4027, -0.2560)
print(f"Home: {home.name if home else '(no places: using New Malden)'} at {lat:.4f}, {lon:.4f}; {places} saved places")
keys = {k: bool(getattr(s, k, "")) for k in ("bods_api_key", "tfl_app_key", "skiddle_api_key", "ticketmaster_api_key",
                                              "datathistle_token", "datathistle_refresh_token", "kitchenowl_token",
                                              "what3words_api_key")}
print("Keys set (yes/no, never the value): " + ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in keys.items()))

get("API health", "/healthz", show=500)
run("Smoke (every page and GET endpoint)", "app.smoke", timeout=300)

# 0.31.0: Air and water
run("Air and water probe (Defra, EA, overflow Hub)", "app.environment")
get("Air and water summary", "/v1/environment/summary", show=3000)
get("Storm overflows", "/v1/environment/overflows")

# 0.32.0 and 0.33.0: Wishlist, Outdoors
if not QUICK:
    run("Wishlist probe (Aldi, Lidl)", "app.wishlist", "kamado", "bbq")
    run("Outdoors probe (GO Outdoors, Bikesy)", "app.outdoors", "105", "cassette")
    run("Outdoors probe (GO Outdoors, Bikesy) 2", "app.outdoors", "two", "man", "tent")
    run("Sigma Sports product page probe", "app.outdoors",
        "https://www.sigmasports.com/item/Shimano/105-HG700-11-speed-Cassette-11-34T/IST8")
get("Wishlist list", "/v1/wishlist", show=2000)
get("Outdoors list", "/v1/outdoors", show=2000)

# 0.33.0 and 0.34.0: Here, next buses, disruptions, bank holidays
get("Here, at home", "/v1/here", show=8000, lat=lat, lon=lon)
get("Next buses at home", "/v1/live-buses/departures", show=4000, lat=lat, lon=lon)
time.sleep(8)  # timetables load in the background: a second look should have them
get("Next buses at home, again", "/v1/live-buses/departures", show=4000, lat=lat, lon=lon)
get("Next buses, Epsom (outside London)", "/v1/live-buses/departures", show=4000, lat=51.3343, lon=-0.2678)
get("Bus disruptions near home", "/v1/bus-disruptions", lat=lat, lon=lon, within_km=5)
get("Bank holidays", "/v1/bank-holidays", show=1500)

# Events, with Data Thistle
get("Events near home (Skiddle, Ticketmaster, Data Thistle)", "/v1/events/near", show=4000, lat=lat, lon=lon, days=7)
if keys["what3words_api_key"]:
    run("what3words probe (the words for home; not counted)", "app.integrations.what3words.client", str(lat), str(lon))
if keys["datathistle_token"]:
    run("Data Thistle probe (one request)", "app.integrations.datathistle.client", str(lat), str(lon))
if not QUICK and keys["bods_api_key"]:
    run("BODS bus line probe (the 406)", "app.integrations.bus.bods_probe", "406", "51.3343", "-0.2678", timeout=240)
    run("BODS disruptions probe", "app.integrations.bods.disruptions_probe", str(lat), str(lon), "25", timeout=240)

head("Summary")
for name, res in results:
    print(f"{res:<45} {name}")
print(f"\n{sum(r.startswith('OK') for _, r in results)} of {len(results)} OK")
PY

echo "Done: $OUT"
grep -A100 "== Summary" "$OUT" | tail -n +3
if [ "$DIR" != "." ]; then  # keep the latest ten
  ls -1t "$DIR"/live-check-*.txt 2>/dev/null | tail -n +11 | xargs -r rm -f
  cp "$OUT" "$DIR/latest.txt"
fi
