# Backups

## What is backed up

Your places, trips, vehicles, train boards, bus tracker settings and the bus history. Fuel prices, road
events, signs, raw captures and job history are left out: the pollers refill them by themselves. A table
added in future is included automatically.

A backup is one `wa-<date>-<time>-v<version>.tar.gz` file in `WA_DATA_DIR/backups` (inside the container,
`/backups`). The latest `WA_BACKUP_KEEP` (default 14) are kept; older ones are deleted. Backups contain
your places, so treat them as private, and copy them somewhere other than this server now and then.

## Taking one

- **On the status page:** press **Back up now**. Each backup has a **Download** button.
- **When you deploy:** `bash scripts/deploy.sh` backs up first, then builds or pulls, starts and checks. If
  the backup fails it stops without changing anything. Use `--skip-backup` only for a first install.
- **By hand:** `docker compose exec api python -m app.backup create` (and `list`).

If the status page says the backup folder is not writable, run `bash scripts/deploy.sh` once (it creates the
folder with the right owner), or: `mkdir -p /mnt/user/appdata/world-assistant/backups && chown 10001:10001 /mnt/user/appdata/world-assistant/backups`.

The first time you run the deploy script on a version that predates this feature, it takes a full
`pg_dump` of the database instead, once.

## Restoring

Restoring replaces the contents of the backed-up tables with the backup, in one transaction: if anything
goes wrong nothing is changed. Use the same release the backup came from, so the database schema matches
(the restore refuses otherwise; `--force` overrides it if you know the tables are compatible).

    docker compose stop worker api
    docker compose run --rm api python -m app.backup list
    docker compose run --rm api python -m app.backup restore wa-20261021-071500-v0.2.0.tar.gz
    docker compose up -d

To restore a downloaded file, copy it into the `backups` folder first. For a brand-new server: start the
stack once so the migrations create the tables, then restore.

Rolling back a release that added a database migration: install the previous release, run the migrations
to its version by starting it on an empty database, then restore. The runbook has the steps.

## OpenBao is not in these backups

If you run the bundled OpenBao (docs/openbao.md), its data lives in `${WA_DATA_DIR}/openbao/file` and is
encrypted with the unseal key. App backups do not include it, and secrets are deliberately never written into
them. Keep your secret files, or a copy of that folder together with the unseal key, somewhere safe.
