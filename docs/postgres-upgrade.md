# Moving the database to PostgreSQL 18

World Assistant's database ran on PostgreSQL 16 with PostGIS 3.4 until 0.24.0. New installs now start on
PostgreSQL 18 with PostGIS 3.6. An existing install stays on 16 until you move it, because a new major version of
PostgreSQL cannot open the old one's data folder: the data has to be copied across. One command does it.

## Upgrading

On the server, in the World Assistant folder, with World Assistant running:

    bash scripts/pg-upgrade.sh

It says what it will do and asks first. Then:

1. It takes a World Assistant backup and stops everything except the database, so nothing writes from here on.
2. It counts every table's rows and copies the whole database (`pg_dump`) into the backups folder.
3. It starts PostgreSQL 18 on a **new** folder, `WA_DATA_DIR/pg18`, and loads the copy into it.
4. It checks every table has the same rows, starts World Assistant and checks every page.

World Assistant is down for a few minutes. The script needs free space for about twice the database, and checks
first.

The old folder, `WA_DATA_DIR/pgdata`, is never changed. **If any step fails, it goes back by itself**: `.env` is put
back and PostgreSQL 16 starts on the old folder, so nothing is lost. Delete `WA_DATA_DIR/pg18` before trying again.

## Afterwards

`.env` has three new lines, which choose the database image and folder. Leave them alone:

    WA_PG_TAG=18-3.6
    WA_PG_DIR=pg18
    WA_PG_MOUNT=/var/lib/postgresql

When you are happy (after a week, say), delete the old folder and the copy:

    rm -rf /mnt/user/appdata/world-assistant/pgdata
    rm /mnt/user/appdata/world-assistant/backups/wa-*-pg16.dump

## Going back

Until you delete the old folder:

    bash scripts/pg-upgrade.sh --rollback

This switches back to PostgreSQL 16 and the old folder. Anything World Assistant saved since the upgrade is not in
that folder, so it will not be there. Your World Assistant backups (Settings, Backups and updates) can bring back places, trips
and so on, as long as you restore them on the same release.

## Why not just change the image?

`postgis/postgis:18` cannot read a PostgreSQL 16 data folder, and from 18 the image keeps its data in
`/var/lib/postgresql/18/docker` rather than `/var/lib/postgresql/data`. Changing the tag would either fail to start
or start an empty database. This is also why the automatic updates never touch the database image.
