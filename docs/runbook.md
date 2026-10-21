# Runbook

## Build locally and run

```bash
cp .env.example .env && bash scripts/init-secrets.sh
docker compose up -d --build
docker compose ps          # all services running / healthy; migrate exited 0
```

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
5. Check `/status`: the version and commit shown must match the release.

## Upgrade and roll back

- **Upgrade:** `bash scripts/deploy.sh` (with `WA_IMAGE` set to the release digest when using a released
  image). It takes a backup first, starts the new version, waits for it to be healthy and shows your
  secrets check. The `migrate` service applies schema changes before api and worker start.
- **Roll back:** set `WA_IMAGE` to the previous digest and run `up -d --no-build`. Migrations are
  forward-only in production; if the release added a migration, restore the backup taken before
  upgrading instead of downgrading (see [backup.md](backup.md)).

## Back up and restore

Backups are built into the app: the status page, `scripts/deploy.sh` and `python -m app.backup`.
See [backup.md](backup.md). A full-database dump is still possible:

```bash
docker compose exec -T wa-db pg_dump -U worldassistant -Fc worldassistant > wa-$(date +%F).dump
docker compose exec -T wa-db pg_restore -U worldassistant -d worldassistant --clean < wa-YYYY-MM-DD.dump
```

## First data load

- **Fuel:** from a stored capture without calling the API again:
  `docker compose run --rm worker python -m app.integrations.fuelfinder.sync --from-capture`.
  Or pull live: `... fuelfinder.sync --pull` (about 4 minutes, UK IP required).
- **Routes:** add places and trips on `/places`, press **Build route** on each. Or
  `docker compose exec worker python -m app.routing build`.

## Secrets

Edit the files in `WA_SECRETS_DIR`, then `docker compose up -d --force-recreate` (containers hold
their own view of the files). To rotate the API token, replace `api_token.txt`, recreate, and use
the new value in the browser.

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `/status` shows 404 | Old image still running: rebuild or pull, `up -d`. |
| A job shows `error` on `/status` | The message is in the job row; `docker compose logs worker`. |
| Fuel login fails with 401 | Client ID and secret may be swapped. Wait 5 minutes before retrying; repeated failures extend the lockout. |
| Fuel pull "empty 200" | Transient; the client retries. If it persists, run with `--probe` (see `fuelfinder.capture --help`). |
| National Highways planned pull incomplete | Rate limit: planned pulls take minutes by design. The job shows `complete: false` and keeps old data. |
| "routing engine unreachable" | Valhalla still building. `docker compose logs -f valhalla`; lower `WA_VALHALLA_THREADS` if it was killed for memory. |
| API returns 401 | Wrong or missing token. 503 means no token is configured. |
| Worker not running jobs | Heartbeat on `/status` is stale: check the worker container and Redis reachability. |

## Switches

`WA_NH_POLL_ENABLED`, `WA_FUEL_POLL_ENABLED`, `WA_NH_CAPTURE_ENABLED`, `WA_NH_LIVE_INTERVAL_MIN`,
`WA_NH_PLANNED_INTERVAL_HOURS`, `WA_FUEL_PRICE_INTERVAL_MIN`: set in `.env`, then `up -d`.

## Moving to the bundled Redis (one time)

Older installs pointed `WA_REDIS_URL` at an existing Redis with no password. To use the stack's own,
password-protected Redis:

1. `bash scripts/init-secrets.sh` (creates `redis_password.txt`; it never overwrites existing files).
2. Delete the `WA_REDIS_URL=` line from `.env`: `sed -i '/^WA_REDIS_URL=/d' .env`
3. `bash scripts/deploy.sh`
4. Check `/status`: the worker heartbeat should be fresh within a minute.

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
updates** switch on the `/status` page. The api cannot restart containers, so the switch is only a flag; a
nightly job on the server (`scripts/auto-deploy.sh`) reads it. With the switch off the job does nothing.

When the switch is on, the job (04:00 every night):

1. pulls the newest release image and stops if it is not newer than what runs now (it never goes back);
2. verifies the release signature (cosign, keyless, from this repository's release workflow) and refuses
   to install if it cannot;
3. refuses if the release needs newer compose files than this folder has (see below);
4. takes a backup, then runs `scripts/deploy.sh`, which updates and runs the page and endpoint check;
5. if that check fails, undoes any database change with the new version's own migrations and starts the
   previous version again;
6. reports the outcome (updated, already up to date, not applied and why, rolled back, needs attention)
   on the status page.

It only installs **released** versions, so a release (yours, or `AUTO_RELEASE`) is still what puts a
change on your server.

**Compose files.** A release image carries the app, not `docker-compose.yml` or the scripts. If this
folder is a git checkout ([above](#updating-the-server-from-github)), the job moves the folder to the
release's tag together with the image, and back again if it has to roll back, so those files always match.
If it is not a checkout, and a release needs newer files (`COMPOSE_REVISION` in the repository is higher
than in your folder), the job refuses and says so on the status page; update the folder and run
`bash scripts/deploy.sh` once.

### One-time setup on Unraid

1. The server must be able to pull the private image: `docker login ghcr.io` (see "Run a released image").
2. Install the **User Scripts** plugin (Apps tab). Settings > User Scripts > Add New Script, name it
   `world-assistant-update`, and paste this as the script:
   ```bash
   #!/bin/bash
   cd /mnt/user/projects/world-assistant && bash scripts/auto-deploy.sh >> /mnt/user/appdata/world-assistant/auto-deploy.log 2>&1
   ```
3. Set its schedule to Custom with `0 4 * * *`, and Apply.
4. Try it once without changing anything:
   `cd /mnt/user/projects/world-assistant && bash scripts/auto-deploy.sh --dry-run`
   (with the switch off it says so; switch it on at `/status` and it says what it would do).
5. Turn the switch on at `/status`. The log is `auto-deploy.log` in the data folder.

If a rollback cannot undo a database change, or the previous version will not start, the job stops and the
status page names the backup to restore ([backup.md](backup.md)); nothing else is touched.

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
