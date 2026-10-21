# Bundled OpenBao key vault (optional)

OpenBao is an open-source fork of HashiCorp Vault. The app already reads secrets from it
(docs/secrets.md); this bundle runs it next to the app so you do not need to install anything else.
It is off unless you run the setup below.

## What you get

* An `openbao` container on the private Docker network only (no port is published, no TLS is needed inside it).
  Its data is in `${WA_DATA_DIR}/openbao/file`.
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

If the log complains of permission denied on `/openbao/file`, run `chmod -R 777 <data dir>/openbao/file`
and run setup again. The image tag defaults to 2.6.4; set `OPENBAO_VERSION` in `.env` for another.

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
