# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).

## [Unreleased]

## [0.6.0] - 2026-10-21

### Added
- This is a new public repository. The project's earlier history (releases 0.1.0 to 0.5.0) stays in a private archive; the first release from here is 0.6.0.
- Documentation brought up to date for a public repository: a README that explains the purpose of the project, current architecture, schema and code layout, data credits and licences, and what automatic updates trust. Issue and pull request templates, and `docs/repo-settings.md` (branch protection for `main` and the other repository settings). Examples and tests use central-London coordinates, never real home ones.
- The server's project folder can be a git checkout of the repository (`scripts/git-setup.sh`, once, with a read-only GitHub token), updated with `scripts/sync.sh [tag|commit] [--deploy]`. The nightly automatic update moves the folder to the release tag along with the image, and back again on a rollback, so compose and script changes arrive too. See docs/runbook.md.
- Automatic updates for the server itself, from a switch on the `/status` page. A nightly job (`scripts/auto-deploy.sh`, set up once with Unraid's User Scripts plugin) updates to the newest signed release when the switch is on: it verifies the signature, takes a backup, updates, runs the page and endpoint check, rolls back (including the database change) if that fails, and reports the result on the status page. A release that needs newer compose files is refused (`COMPOSE_REVISION`). New `/v1/auto-deploy` endpoints and an `app_setting` table (migration 0014). See docs/runbook.md.
- Comfort tests. CI has a new `stack` job that starts the real compose stack on PostGIS (pollers off), runs the migrations, requests every page and every GET endpoint (`python -m app.smoke`: no 500s, pages complete, the API refuses a missing or wrong token, a missing key gives a clear 503, never a crash), takes the last migration down and up, does a backup, delete and restore round trip, and loads every page in a real browser with the token entered (`scripts/page-check.py`, no script errors). The release gate and automatic merging now require `stack` as well as `test`, `secrets` and `image`. `deploy.sh` runs the read-only smoke check once the api is healthy and fails if anything does. `tests/test_migrations_chain.py` guards against two migration heads.
- Optional automatic updates (repository variable `AUTO_UPDATES=true`): safe Dependabot updates merge once CI passes, CI runs on `main` after, and a changed Python base image opens a GitHub issue. A second variable, `AUTO_RELEASE=true`, also releases the next patch version through the gated release. Dependabot now checks daily and also watches the compose files. Releases record their base image. `deploy.sh` pulls the latest third-party and base images. See docs/runbook.md.

### Changed
- The worker records its heartbeat as soon as it starts, so `/status` shows it alive straight after a deploy instead of up to five minutes later.
- The release workflow is gated: it refuses unless the commit is the tip of `main`, the version is new and CI (`test`, `secrets`, `image`) has passed on it, waiting up to 5 minutes for CI. No override. See docs/runbook.md.

## [0.5.0] - 2026-10-21

### Changed
- Redis is now part of the stack: a password-protected `wa-redis` with no published port, password in the new `redis_password` secret (`init-secrets.sh` creates it). `WA_REDIS_URL` is optional; remove it from `.env` to use the bundled one. See docs/runbook.md.
- Home is the default place: the fuel, events and pollen endpoints and assistant tools no longer need a `place` (they use your home), and the `/fuel`, `/events` and `/pollen` pages start on it. See docs/assistant.md.

### Added
- Pollen: pollen levels by allergen and the allergy risk near a saved place, today and the next three days, from the Austrian Pollen Information Service (free personal key in `pollen_api_key`; non-commercial use, credit shown). `/pollen` page, `/v1/pollen/near` (add `hourly=true` for hourly risk), assistant tool `pollen`, probe `python -m app.integrations.pollen.probe`. Cached for 4 hours.
- Pollen history: a worker job fetches for every saved place every 4 hours and records one value per day and pollen, kept for 90 days. `/pollen` shows a total graph and a graph per pollen (with the allergy risk), and `/v1/pollen/history` returns the data. `python -m app.pollen_history` records once, now. New migration 0013.

## [0.4.0] - 2026-10-21

### Added
- Events: what is on near a saved place, from Skiddle and Ticketmaster (free keys in `skiddle_api_key` and
  `ticketmaster_api_key`; either alone works), merged and sorted by date; one source failing still shows the other. `/events` page,
  `/v1/events/near`, assistant tool `events_near`, and probes for each source (`python -m app.integrations.skiddle.probe`, `...ticketmaster.probe`).
  Results are cached for 10 minutes and never stored.
- OpenBao, bundled and optional: `docker-compose.openbao.yml` runs it beside the app, and
  `bash scripts/openbao.sh setup` initialises it, creates the KV store and a read-only AppRole, copies your
  secret files in and points the app at it. Unseal keys and the root token are shown once and never stored;
  `deploy.sh` unseals after upgrades only if you opt in with `store-key`. See docs/openbao.md.
- Optional auto-unseal for the bundled OpenBao: `scripts/openbao.sh store-key` starts a helper that unseals it after any restart.
- `python -m app.secretsctl dump-json` (refuses a terminal) for moving secrets into a vault.

## [0.3.0] - 2026-10-21

### Added
- Backups: built in, no extra tools. A **Back up now** card with downloads on `/status`, `/v1/backups`,
  `python -m app.backup create|list|restore`, and `scripts/deploy.sh`, which backs up before every
  upgrade and stops if the backup fails. Keeps the latest 14. Restores are one transaction. See docs/backup.md.
- Fuel: the cheapest station nearby, any brand, is always shown at the top of `/fuel` and returned as
  `cheapest_overall` by the API, whatever brands are ticked. New "prices no older than" control.

### Fixed
- Fuel: brand spellings from the feed are merged (`SAINSBURYS`, `Sainsbury's`), shown evenly, and the major
  supermarket brands count as supermarkets whatever the feed's flag says.

## [0.2.0] - 2026-10-20

### Changed
- Secrets: one lookup for every key and password (`app/secretstore.py`). Files in the secrets directory
  (with or without `.txt`) remain the default; optional key vault backends: HashiCorp Vault / OpenBao
  (KV v2, token or AppRole) and a generic command backend for 1Password, Bitwarden, Azure, AWS, Google
  and others. Vault outage falls back to the last values and then to files. New
  `python -m app.secretsctl check` shows where each secret comes from without printing values.
  `init-secrets.sh` now also creates `nr_access_token`; vault sign-in files are only mounted via the optional `docker-compose.vault.yml`. See docs/secrets.md.
- All browser pages (`/status`, `/bus`, `/trains`, `/fuel`, `/places`) share one cleaner look with a nav bar,
  cards and automatic dark mode. No data or API changes.

### Added
- Fuel: tick brands (or all supermarkets) on `/fuel` to compare the absolute cheapest with the cheapest of your
  chosen brands, with the extra pence per litre, the extra cost for a fill and the distance saved.
  API: `brands=Tesco,Asda`, `supermarkets=true`, `fill_litres` on `/v1/fuel/near` and `/along`; the
  assistant `fuel_cheapest` tool takes `brands` and `supermarkets`.
- Bus tracker: follows every bus on a line, times legs between configurable stops of interest,
  records which buses reached a target stop before a deadline (weekday polling window), captures
  terminations and turnarounds, slot reliability history. See docs/bus.md.
- Question answering: `/mcp` (MCP over HTTP) and `/v1/ask/{tool}` (Siri Shortcuts, scripts) for fuel,
  trip cost, trains and buses, with spoken-sentence answers. See docs/assistant.md.
- Rail departures from National Rail (any GB station, via the Rail Data Marketplace) as well as TfL: boards have a `source`; probe tool
  `python -m app.integrations.rail.probe`.
- `/trains`: optional display grouping by platform (browser-only setting; the data is unchanged).
- `/bus` page: live buses, target-stop run, leg times, reliability, events, and guided tracker setup
  (pick stops from a list; ids, direction and return side are worked out).
- MQTT publishing with Home Assistant discovery for bus tracker sensors.
- TfL departures: saved boards (station, lines, destination filter), live departures with delay and
  status, line status, station search, `/trains` page, probe tool.

### Fixed
- Train boards keep showing the last good reading (up to 15 minutes) when the provider errors.
- Rebuilding a route could fail with a 500 (duplicate corridor tag for an event); tagging is now idempotent.

### Changed
- Release: GitHub provenance attestation is optional (`ENABLE_GH_ATTESTATIONS`), since private personal repos cannot store it.
- All GitHub Actions moved to their latest releases (Node 24) and pinned to commit hashes.

### Fixed
- CodeQL workflow no longer fails on a private repository: it runs only when `ENABLE_CODEQL` is set, and uses CodeQL Action v4.

## [0.1.0] - 2026-10-20

First tracked release.

### Added
- National Highways adapter: closures, speed managed areas and message signs, polled
  (live every 10 minutes, planned every 2 hours), stored with history, tagged by corridor.
- Fuel Finder adapter: stations (daily) and prices (every 30 minutes), change history,
  cheapest-near-place and cheapest-along-corridor queries.
- Places, trips and vehicles with web pages and API; postcode lookup.
- Valhalla routing: trip to route to corridor; base journey time and road numbers.
- Trip cost for petrol/diesel and electric vehicles; detour-aware station ranking.
- Status page and API, job tracking, bearer-token authentication.
- Hash-locked dependencies, multi-stage non-root image, hardened compose override.
- CI, release workflow (build once, scan, SBOM in SPDX and CycloneDX, keyless signing,
  provenance), CodeQL, secret scanning, Dependabot.
