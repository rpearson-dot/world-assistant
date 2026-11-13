# Runbook

## Build locally and run

```bash
bash scripts/setup.sh      # asks where things live, about Home Assistant and routing, and for keys; then starts
docker compose ps          # all services running / healthy; migrate exited 0
```

Or by hand: `cp .env.example .env`, edit it, `bash scripts/init-secrets.sh`, put keys in the secret files,
`docker compose up -d --build`. Re-run `setup.sh` any time to change answers; Enter keeps each current value.

Optional extras: add `-f docker-compose.routing.yml` for Valhalla and
`-f docker-compose.hardened.yml` for the read-only, capability-dropped containers.

## Run a released image (immutable)

Releases are published to `ghcr.io/rpearson-dot/world-assistant`. Deploy by digest, never by tag.
To make a release: update `CHANGELOG.md`, merge to `main`, wait for CI, then GitHub > Actions >
Release > Run workflow and enter the version (for example `0.1.0`). It creates the tag `vX.Y.Z`.

The release is **gated** (`scripts/release-gate.sh`, no override). It refuses to start unless the commit is
the tip of `main`, the version has not been released before, and the CI checks `test`, `secrets` and
`image` have succeeded on that commit. If CI is still running it waits up to 5 minutes; if a check has
failed it stops at once. To retry after a red build, fix it on `main` and run the release again.

1. Find the digest in the GitHub release notes and verify the signature
   ([docs/security.md](security.md)).
2. One-off, only while the image package is private (GitHub > your profile > Packages > the package >
   Package settings > Change visibility makes it public and this step unnecessary): log in so the host can
   pull it, using a read-only token (`read:packages`) created for this purpose:
   `echo "$TOKEN" | docker login ghcr.io -u rpearson-dot --password-stdin`
3. Set the image in `.env`: `WA_IMAGE=ghcr.io/rpearson-dot/world-assistant@sha256:<digest>`
4. Deploy without building:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.hardened.yml pull
   docker compose -f docker-compose.yml -f docker-compose.hardened.yml up -d --no-build
   ```
5. Check Settings > System: the version and commit shown must match the release.

## Upgrade and roll back

- **Update now, the same way as the nightly update:** `bash scripts/auto-deploy.sh --now` (newest release, signature
  checked, backup, page check, rollback on failure; works with the switch off).
- **Pinned image and files kept together:** with `WA_IMAGE` set, `scripts/sync.sh` takes the newest release (not main;
  `--main` for main), and `scripts/deploy.sh` moves the pin to the files' release if they differ, or stops and says why.
- **Upgrade:** `bash scripts/deploy.sh` (with `WA_IMAGE` set to the release digest when using a released
  image). It takes a backup first, then updates in stages with no gap when the stack is running
  ([staged updates](#staged-updates-no-gap)), or starts everything when it is not. It shows your secrets check.
  `--infra` brings Postgres, Redis and the other containers to their newest images (the nightly update does that).
- **Roll back:** set `WA_IMAGE` to the previous digest and run `up -d --no-build`. Migrations are
  forward-only in production; if the release added a migration, restore the backup taken before
  upgrading instead of downgrading (see [backup.md](backup.md)).

## Moving the database to PostgreSQL 18 (one time)

Installs from before 0.24.0 run PostgreSQL 16. `bash scripts/pg-upgrade.sh` copies the data to PostgreSQL 18 on a
new folder, checks every row and page, and goes back by itself if anything fails. See
[docs/postgres-upgrade.md](postgres-upgrade.md).

## Back up and restore

Backups are built into the app: Settings (Backups and updates), `scripts/deploy.sh` and `python -m app.backup`.
See [backup.md](backup.md). A full-database dump is still possible:

```bash
docker compose exec -T wa-db pg_dump -U worldassistant -Fc worldassistant > wa-$(date +%F).dump
docker compose exec -T wa-db pg_restore -U worldassistant -d worldassistant --clean < wa-YYYY-MM-DD.dump
```

## First data load

On a new install the worker loads fuel stations (with a Fuel Finder key), bus stops and stations, and
postcodes as soon as it starts, rather than at the nightly run. The Setup list in Settings shows them
arrive. By hand:

- **Fuel:** from a stored capture without calling the API again:
  `docker compose run --rm worker python -m app.modules.fuel.sources.fuelfinder.sync --from-capture`.
  Or pull live: `... fuelfinder.sync --pull` (about 4 minutes, UK IP required).
- **Routes:** add places and trips on `/places`, press **Build route** on each. Or
  `docker compose exec worker python -m app.routing build`.

## Secrets

Edit the files in `WA_SECRETS_DIR`, then `docker compose up -d --force-recreate` (containers hold
their own view of the files). To rotate the API token, replace `api_token.txt`, recreate, and use
the new value in the browser.

## Unraid Docker tab icons

The compose files label each container with an icon (`net.unraid.docker.icon`) and the api with a
WebUI link, so they look native in Unraid's Docker tab. Unraid downloads each icon from the api
(`http://127.0.0.1:8010/icons/<name>.png`, or your `WA_API_PORT`) when it first sees the container, so
after an update that adds them, recreate the containers (`docker compose up -d --force-recreate`) and
refresh the Docker tab. If an icon does not appear, open that address in a browser on the NAS to check
it loads. Unraid caches icons, and reads label changes only on container creation. The pictures are
drawn by `deploy/unraid/make_icons.py` (SVG in `deploy/unraid/icons-src/`, rendered to `app/icons/`
with `deploy/unraid/render_icons.py`).

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `/settings` shows 404 | Old image still running: rebuild or pull, `up -d`. |
| A job shows `error`  in Settings | The message is in the job row; `docker compose logs worker`. |
| Fuel login fails with 401 | Client ID and secret may be swapped. Wait 5 minutes before retrying; repeated failures extend the lockout. |
| Fuel pull "empty 200" | Transient; the client retries. If it persists, run with `--probe` (see `fuelfinder.capture --help`). |
| National Highways planned pull incomplete | Rate limit: planned pulls take minutes by design. The job shows `complete: false` and keeps old data. |
| "routing engine unreachable" | Valhalla still building. `docker compose logs -f valhalla`; lower `WA_VALHALLA_THREADS` if it was killed for memory. |
| API returns 401 | Wrong or missing token. 503 means no token is configured. |
| Worker not running jobs | Heartbeat in Settings > System is stale: check the worker container and Redis reachability. |

## Switches

`WA_NH_POLL_ENABLED`, `WA_FUEL_POLL_ENABLED`, `WA_NAPTAN_POLL_ENABLED`, `WA_FLOOD_POLL_ENABLED`, `WA_CODEPOINT_POLL_ENABLED`, `WA_NH_CAPTURE_ENABLED`, `WA_NH_LIVE_INTERVAL_MIN`,
`WA_NH_PLANNED_INTERVAL_HOURS`, `WA_FUEL_PRICE_INTERVAL_MIN`: set in `.env`, then `up -d`.

## Moving to the bundled Redis (one time)

Older installs pointed `WA_REDIS_URL` at an existing Redis with no password. To use the stack's own,
password-protected Redis:

1. `bash scripts/init-secrets.sh` (creates `redis_password.txt`; it never overwrites existing files).
2. Delete the `WA_REDIS_URL=` line from `.env`: `sed -i '/^WA_REDIS_URL=/d' .env`
3. `bash scripts/deploy.sh`
4. Check Settings > System: the worker heartbeat should be fresh within a minute.

Nothing is lost: Redis only holds rate-limit counters, caches and the heartbeat, which rebuild themselves.
The old Redis is no longer used by this app; stop it if nothing else needs it.

## Comfort tests

Three layers, so a bad update is caught before it reaches you:

- **CI `stack` job** (every push and pull request): brings up the real stack on PostGIS with throwaway
  secrets, runs the migrations, hits every page and every GET endpoint, takes the latest migration down and
  up, does a backup, delete and restore, and opens each page in a browser. Run it yourself on a clean
  checkout with `bash scripts/ci-stack.sh` (needs Docker; refuses to run if a `.env` exists).
- **Release gate and automatic merging** both require `stack` to be green, alongside `test`, `secrets`
  and `image`.
- **After every deploy**, `scripts/deploy.sh` runs `docker compose exec -T api python -m app.smoke`. It is
  read-only: every page returns a complete HTML page, the API refuses a missing or wrong token, the worker
  heartbeat is alive, and no GET endpoint returns a 500. A feature without its key (events, pollen) is fine
  if it says so with a 503 and a reason. Run it any time; add `--seed` only on a test copy, as it saves
  and removes a test place.

## Automatic updates (optional, off by default)

Two repository variables (GitHub > Settings > Secrets and variables > Actions > Variables) control this:

- `AUTO_UPDATES` = `true` turns on the daily checks and the automatic merging of safe updates.
- `AUTO_RELEASE` = `true` (only on top of `AUTO_UPDATES`) also releases the next patch version by itself.
  Leave it unset to keep releasing by hand.

Delete a variable, or set anything else, to turn that part off.

When `AUTO_UPDATES` is on, the **Automatic updates** workflow runs daily, and also after Dependabot's pull
requests finish CI:

1. It merges open Dependabot pull requests that have passed CI **and** are safe: patch updates of Python
   libraries, Docker base images and compose images, and minor updates of Python libraries. Major updates
   are left for you, and so is anything that holds your data (Postgres/PostGIS: a new major version needs a
   dump and restore). Docker and compose images are judged by their version numbers: only a patch-level
   change is merged. GitHub Actions updates are left for you too (the built-in token may not change
   workflow files), so merge those from the pull request.
2. After a merge it runs CI on `main`, so a bad update shows up as a red build.
3. It checks whether the Python base image has changed since the last release (each release records it in
   its notes), which catches security patches that keep the same tag. If it has, it opens a GitHub issue
   ("release when ready") once, and you release when you choose.
4. With `AUTO_RELEASE` on, it instead waits for CI and releases the next patch version through the normal
   gate. A red CI means no release.

Dependabot checks daily for Python libraries, the Dockerfile, compose images and GitHub Actions.

Your server is not changed by any of this. A release is a new signed image; you pick it up with
`bash scripts/deploy.sh`, which also pulls the latest Redis, Postgres, OpenBao and base images each time.
Merged updates go to `main` without a human reading them, so a bad update shows up as a red CI on `main`
(GitHub emails you); nothing reaches your server until you deploy.

## Automatic updates on this server (optional, off by default)

The section above keeps GitHub up to date. This one updates the server itself, from the **Automatic
updates** switch and the **Update now** button on the Settings page (`/settings`, Backups and updates). The api
cannot restart containers, so both are only stored requests. The **updater** container (`wa-updater`) acts on
them: every night at 04:00 when the switch is on, and within a minute of Update now (whether the switch is on or
off). Both run `scripts/auto-deploy.sh`.

The update:

1. pulls the newest release image and stops if it is not newer than what runs now (it never goes back);
2. verifies the release signature (cosign, keyless, from this repository's release workflow) and refuses
   to install if it cannot;
3. refuses if the release needs newer compose files than this folder has (see below);
4. takes a backup, then runs `scripts/deploy.sh`, which updates **in stages, with no gap** (below);
5. reports the outcome (updated, already up to date, not applied and why, rolled back, needs attention)
   on the Settings page;
6. once it is up, starts the **live check** in the background (`scripts/live-check.sh`): every outside source
   World Assistant uses, and its own endpoints for home, asked live, into `WA_DATA_DIR/live-checks/latest.txt`
   (the last ten are kept). It is a record to read, never a reason to roll back, since other people's services
   are down sometimes. `WA_LIVE_CHECK=0` turns it off; `bash scripts/live-check.sh` runs it by hand;
7. removes release images older than the one before (that one stays, to go back to);
8. at night only: brings Postgres, Redis, the front door, Tailscale and ntfy to their newest images
   (`scripts/deploy.sh --infra`). Only containers with a newer image restart, briefly, at 04:00.

It only installs **released** versions, so a release (yours, or `AUTO_RELEASE`) is still what puts a
change on your server.

### Staged updates (no gap)

Every request reaches World Assistant through its front door, `wa-proxy` (Caddy; it holds the published port, and
Tailscale and Traefik point at it too). An update (`scripts/rollout.sh`, about 13 minutes):

1. runs the new version's database changes. They only ever add ([migrations.md](migrations.md)), so the running
   version carries on;
2. starts the new version beside the running one. It must become healthy and pass the page and endpoint check
   before it sees a request;
3. **trial:** one request in five goes to it for 2 minutes (`WA_CANARY_S`). A server error, or it falling ill,
   sends everything back;
4. every request goes to it, while the old version stands by for 10 minutes (`WA_KEEP_S`), watched the same way.
   Going back is one switch at the front door, with nothing restarted;
5. the new worker starts beside the old one, which finishes its running jobs (up to 2 minutes) before it stops,
   and the api service itself is replaced and takes the requests back.

If the new version fails a check at any point up to step 5, the old one has answered every request throughout and
keeps running. The update reports "rolled back", and the database keeps what the new version added, which the old
one ignores. Nothing needs restoring.

The first update to the version with the front door replaces everything at once, as before (a minute's gap), and
so does `bash scripts/deploy.sh --classic`. If that kind of update fails its checks, the job undoes any database
change with the new version's own migrations and starts the previous version again.

**Compose files.** A release image carries the app, not `docker-compose.yml` or the scripts. If this
folder is a git checkout ([below](#updating-the-server-from-github)), the job moves the folder to the
release's tag together with the image, and back again if it has to roll back, so those files always match.
If it is not a checkout, and a release needs newer files (`COMPOSE_REVISION` in the repository is higher
than in your folder), the job refuses and says so on the Settings page; update the folder and run
`bash scripts/deploy.sh` once.

### The updater container

`scripts/deploy.sh` starts it. On the first run it adds two lines to `.env`: `COMPOSE_PROFILES=updater` and
`WA_PROJECT_DIR` (this folder's path). It replaces the User Scripts this runbook used to set up.

- **What it can do:** it drives Docker through the server's Docker socket, which is full control of the server.
  So it publishes no port and only talks to World Assistant on the stack's own network. It installs only releases
  whose signature checks out, and only runs the scripts in this folder.
- **Logging in:** it pulls the private image with the server's own login (`docker login ghcr.io`, once; see "Run a
  released image"), read from `/root/.docker` (`WA_DOCKER_CONFIG` if yours is elsewhere).
- **Log:** `auto-deploy.log` in the data folder, as before.
- **Moving the nightly run:** `WA_UPDATE_AT=HH:MM` in `.env` (default 04:00).
- **Turning it off:** `WA_UPDATER=off` in `.env`, remove `updater` from `COMPOSE_PROFILES`, then
  `docker compose rm -sf wa-updater`. A User Script can then run `bash scripts/auto-deploy.sh` (nightly) and
  `bash scripts/auto-deploy.sh --if-requested` (every minute) instead.
- **Old User Scripts:** delete `world-assistant-update` (and `world-assistant-update-now`, if you made it) under
  Settings > User Scripts. Left in place they do no harm: only one update runs at a time, and the second finds
  nothing to do.

To try it without changing anything: `docker compose exec wa-updater bash scripts/auto-deploy.sh --dry-run`.

A press of Update now can be cancelled until the updater picks it up. If a run dies without reporting, the press is
dropped after 45 minutes, so the button does not stay stuck. If the previous version will not start, the job
stops and the Settings page names the backup to restore ([backup.md](backup.md)); nothing else is touched.

## Updating the server from GitHub

The project folder on the server is a git checkout of the repository, so nothing is copied by hand:

```bash
cd /mnt/user/projects/world-assistant
bash scripts/sync.sh --deploy          # newest main, then deploy
bash scripts/sync.sh v0.6.0 --deploy   # a particular release
```

`.env`, secrets and data are not in the repository and are never touched. Do not edit tracked files on the
server: syncing replaces them.

### One-time setup

1. **Only while the repository is private** (skip to step 3 once it is public): create a read-only token. On GitHub: your profile picture > Settings > Developer settings > Personal
   access tokens > Fine-grained tokens > Generate new token. Name it `world-assistant-server`, set an
   expiry (a year; put a reminder in your calendar), **Repository access: Only select repositories** and
   choose `World-assistant`, then under Repository permissions set **Contents: Read-only**. Generate it and
   keep the page open.
2. On the server, save it without it appearing on screen or in your shell history:
   ```bash
   read -rs -p "Paste the token, then press Enter: " T; echo
   printf '%s' "$T" > /mnt/user/appdata/world-assistant/secrets/github_token.txt
   chmod 600 /mnt/user/appdata/world-assistant/secrets/github_token.txt; unset T
   ```
3. Turn the folder into a checkout (it keeps a tar copy of the folder first):
   ```bash
   cd /mnt/user/projects/world-assistant && bash scripts/git-setup.sh
   ```
   Unraid has no `git` of its own; the scripts use the `alpine/git` container instead (pulled
   automatically), or the `git` from the NerdTools plugin if you have installed it.

When the token expires, create a new one and repeat step 2.
