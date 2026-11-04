# Bundled OpenBao key vault (optional)

OpenBao is an open-source fork of HashiCorp Vault. The app already reads secrets from it
(docs/secrets.md); this bundle runs it next to the app so you do not need to install anything else.
It is off unless you run the setup below.

## What you get

* An `openbao` container on the private Docker network only (no port is published, no TLS is needed inside it).
  Its data is in `${WA_DATA_DIR}/openbao/raft` (a vault set up before 0.26.0: `openbao/file` until you upgrade it, below).
* A KV store at `secret/world-assistant` holding every key and password the app uses.
* An AppRole that can only **read** that one path. The app signs in with it (`vault_role_id.txt` and
  `vault_secret_id.txt`).
* Your secret files stay in place as a fallback while the vault is unreachable or sealed.

`postgres_password` and the vault sign-in files are never copied in: the database and the vault itself need
them before any vault can be read.

## Set it up (once)

1. Update and rebuild so the app has the import command: `bash scripts/deploy.sh`
2. `bash scripts/openbao.sh setup`
3. When the box of keys appears, **copy the Unseal Key and Initial Root Token into your password manager,
   on a different device or account from this server.** They are shown once and written nowhere.
   Press Enter when saved.
4. Setup finishes by telling you to restart the app: `bash scripts/deploy.sh --skip-backup`. Do that.
5. Check: `docker compose exec api python -m app.secretsctl check` should say `from vault`.

If the log complains of permission denied on `/openbao/raft`, run `chmod -R 777 <data dir>/openbao/raft`
and run setup again. A new vault runs OpenBao 2.7.2 on raft storage (`OPENBAO_VERSION` and `OPENBAO_CONFIG` in `.env`).

## After a restart: unsealing

OpenBao starts sealed and cannot give out anything until you unseal it. Until then the app uses its secret
files, so nothing stops.

* By hand (safest): `bash scripts/openbao.sh unseal`, paste the key when asked (hidden).
* Automatic (convenient): `bash scripts/openbao.sh store-key` keeps one unseal key in
  `secrets/openbao_unseal_key.txt` (mode 600) and starts a small helper container (`openbao-unseal`) that
  unseals OpenBao within about 10 seconds of any restart, including a NAS reboot. The key then sits beside the
  vault data, so this protects against a leaked backup or a stolen disk image of the data folder, not against
  someone who has the whole server. Remove it with `forget-key`.

## Changing or adding a secret

A key saved on the Settings page's Keys tab is kept on the server, outside the vault. With the vault set up, the Keys
tab shows **Move the keys into the vault**: paste a token that can write (the root token, or an admin one; it is used
once and not kept) and every key saved on the page or in a secret file is copied into the vault, checked through the
app's own login, and the page's copies are forgotten. Secret files on the server are left for you to delete.


Edit the secret file, then `bash scripts/openbao.sh import` (asks for the root token, hidden). The app picks
it up within 5 minutes. Or use the UI inside the Docker network, or `bao kv patch` in the container.

## Other commands

`bash scripts/openbao.sh status` shows whether it is initialised, sealed, and whether automatic unsealing is on.
If the container restarts in a loop, read its log with `docker logs --tail 30 world-assistant-openbao-1`.
Use `setup --shares=5 --threshold=3` for a split key (then `store-key` is not suitable).

## Limits

* One node, file storage, no TLS inside the Docker network. Fine for a home server; do not publish port 8200.
* Not in the app backups (docs/backup.md). Losing the unseal key means losing the vault contents; your
  secret files remain the way back.

## Upgrading a vault set up before 0.26.0 (once)

Vaults set up before World Assistant 0.26.0 run OpenBao 2.6 on "file" storage. OpenBao 2.6 stopped getting fixes on
1 October 2026, and 2.7 can no longer read file storage, so the data has to move to raft storage first. One command
does both:

    bash scripts/openbao.sh upgrade

It copies `openbao/file` into the backups folder, moves the data to `openbao/raft` (`bao operator migrate`), and
starts OpenBao 2.7.2 on it. The vault is down for about a minute and comes back sealed: it unseals by itself if the
key is stored on the server (`store-key`), otherwise it asks for the unseal key. The unseal key and root token do
not change. If anything fails it goes back to file storage as it was. `openbao/file` is only read, and kept: delete
it, and the copy in the backups folder, once you are happy.
