#!/usr/bin/env bash
# Live check: ask every outside source World Assistant uses, and its own endpoints for home, and write what comes
# back to one text file to read or send on. Read-only (apart from the Wishlist and Outdoors checks, which record what
# they find, as their morning runs do). Uses about two of Data Thistle's monthly requests.
#
#     bash scripts/live-check.sh                 # writes live-check-<date>-<time>.txt here
#     bash scripts/live-check.sh --quick         # skips the slower shop and timetable checks
#     bash scripts/live-check.sh --out DIR       # writes it in DIR instead, keeping the latest ten there
#
# A check that belongs to a module switched off at /modules is not run: it shows as OFF in the summary and is not a
# failure (its pages answer "switched off", and its shops and sources should not be asked).
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


def switched_on() -> set[str] | None:
    """The modules switched on now (None if that cannot be read: then everything is checked)."""
    try:
        from app.modules import registry, selection

        mods = list(registry.modules())
        with create_engine(s.database_url).connect() as c:
            raw = c.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": selection.KEY}).scalar()
        return selection.enabled_ids(mods, selection.Selection.from_json(raw) if raw else selection.Selection())
    except Exception as exc:  # noqa: BLE001
        print(f"(could not read which modules are on: {type(exc).__name__}; checking everything)")
        return None


ON = switched_on()


def off(name: str, module: str | None) -> bool:
    """True (and noted) when the check's module is switched off."""
    if module and ON is not None and module not in ON:
        results.append((name, f"OFF  ({module} is switched off at /modules: not checked)"))
        return True
    return False


def head(t: str) -> None:
    print("\n" + "=" * 100 + f"\n== {t}\n" + "=" * 100, flush=True)


def note(name: str, ok: bool, why: str = "") -> None:
    results.append((name, ("OK   " if ok else "FAIL ") + why))


def run(name: str, *args: str, timeout: int = 180, module: str | None = None, known: str = "") -> None:
    """known: why this check is expected to fail (shown as INFO, not counted as a failure)."""
    if off(name, module):
        return
    head(f"{name}:  python -m {' '.join(args)}")
    t = time.time()
    try:
        p = subprocess.run([sys.executable, "-m", *args], capture_output=True, text=True, timeout=timeout)
        out = (p.stdout + p.stderr).strip()
        print(out[:12000] + ("\n... (cut)" if len(out) > 12000 else ""))
        failed = p.returncode != 0 or "FAILED" in out or "Traceback" in out
        if failed and known and "Traceback" not in out:
            results.append((name, f"INFO {time.time() - t:.0f}s: {known}"))
            return
        note(name, not failed, f"{time.time() - t:.0f}s" + (" (see FAILED or Traceback above)" if failed else ""))
    except subprocess.TimeoutExpired:
        print(f"timed out after {timeout}s")
        note(name, False, f"timed out after {timeout}s")


def get(name: str, path: str, show: int = 4000, **params: object) -> object:
    from app.modules import registry

    if off(name, registry.module_for_path(path)):
        return None
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
run("Air and water probe (Defra, EA, overflow Hub)", "app.environment", module="environment")
get("Air and water summary", "/v1/environment/summary", show=3000)
get("Storm overflows", "/v1/environment/overflows")

# 0.32.0 and 0.33.0: Wishlist, Outdoors
if not QUICK:
    run("Wishlist probe (Aldi, Lidl)", "app.wishlist", "kamado", "bbq", module="wishlist")
    run("Outdoors probe (GO Outdoors, Bikesy)", "app.outdoors", "105", "cassette", module="outdoors")
    run("Outdoors probe (GO Outdoors, Bikesy) 2", "app.outdoors", "two", "man", "tent", module="outdoors")
    run("Sigma Sports product page probe", "app.outdoors",
        "https://www.sigmasports.com/item/Shimano/105-HG700-11-speed-Cassette-11-34T/IST8", module="outdoors")
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
    run("what3words probe (the words for home; not counted)", "app.integrations.what3words.client", str(lat), str(lon),
        module="here")
if keys["datathistle_token"]:
    run("Data Thistle probe (one request)", "app.integrations.datathistle.client", str(lat), str(lon), module="events")
if not QUICK and keys["bods_api_key"]:
    run("BODS bus line probe (the 406)", "app.integrations.bus.bods_probe", "406", "51.3343", "-0.2678", timeout=240,
        module="bods", known="TfL's buses publish no timetable to BODS; live times come from TfL")
    run("BODS disruptions probe", "app.integrations.bods.disruptions_probe", str(lat), str(lon), "25", timeout=240,
        module="busdisruptions")

# 0.39.0: the bike garage (its rides' wet suggestion comes from the EA rain gauges)
riders = get("Bike riders", "/v1/bikes/riders", show=1000)
if isinstance(riders, dict) and riders.get("riders"):
    get("Bike garage (the first rider)", "/v1/bikes", show=3000, rider=riders["riders"][0]["id"])
run("Bike rain probe (the nearest EA gauge, yesterday morning)", "app.bikes.rain", str(lat), str(lon), module="bikes")
# stage 2: Garmin Connect's Personal API (each rider's key): what it gives, then the garage's sync state
run("Garmin probe (rides, history, gear, GPS, sensors, weight)", "app.bikes.garmin", module="bikes", timeout=300)
if isinstance(riders, dict):
    for r in riders.get("riders") or []:
        get(f"Garmin sync state ({r['id']})", "/v1/bikes/garmin", show=1500, rider=r["id"])

run("Recalls probe (GOV.UK product safety recalls for a few bike makers)", "app.bikes.recalls", "SRAM", "Shimano", "Yeti", module="bikes")
# 0.42.0: crime and police, food standards, tax and MOT
run("Police probe (crime within a mile of home, the last 2 months; the police team)", "app.crime", str(lat), str(lon), module="crime")
get("Crime near home (12 months)", "/v1/crime", show=2500, lat=lat, lon=lon)
run("Food standards probe (hygiene near home, alerts of the last 30 days)", "app.food", str(lat), str(lon), module="food")
get("Food alerts and matches", "/v1/food/alerts", show=2500, limit=5)
checks = get("Tax and MOT", "/v1/vehicle-checks", show=3000)
regs = [v.get("registration") for v in (checks or {}).get("vehicles") or []] if isinstance(checks, dict) else []
run("DVLA and DVSA probe (the first registration)", "app.vehiclecheck", *(regs[:1] or [""]), module="vehiclechecks")

head("Summary")
for name, res in results:
    print(f"{res:<45} {name}")
n_off = sum(r.startswith("OFF") for _, r in results)
n_info = sum(r.startswith("INFO") for _, r in results)
checked = len(results) - n_off - n_info
print(f"\n{sum(r.startswith('OK') for _, r in results)} of {checked} OK" + (f"; {n_off} not checked (switched off)" if n_off else "")
      + (f"; {n_info} known gaps (INFO)" if n_info else ""))
PY

echo "Done: $OUT"
grep -A100 "== Summary" "$OUT" | tail -n +3
if [ "$DIR" != "." ]; then  # keep the latest ten
  ls -1t "$DIR"/live-check-*.txt 2>/dev/null | tail -n +11 | xargs -r rm -f
  cp "$OUT" "$DIR/latest.txt"
fi
