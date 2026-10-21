# Secrets and key vaults

Every API key and password is read through one function (`app/secretstore.py`). You configure where
they live in one place and nothing else in the code knows or cares.

## Default: one directory of files

`WA_SECRETS_DIR` (default `/mnt/user/appdata/world-assistant/secrets`) holds one file per secret, named
`<secret>.txt` (a bare `<secret>` also works). `bash scripts/init-secrets.sh` creates the directory, generates
the database password and API token, and leaves empty files for the keys you add yourself.

| Secret | Needed | Used for |
|---|---|---|
| `api_token` | yes | Bearer token for the API, the pages, MCP and Siri endpoints |
| `postgres_password` | yes | Database password |
| `nh_subscription_key`, `nh_subscription_key_secondary` | for road data | National Highways |
| `fuel_finder_client_id`, `fuel_finder_client_secret` | for fuel prices | Fuel Finder |
| `tfl_app_key` | optional | Higher TfL rate limit |
| `nr_access_token` | for National Rail | Rail Data Marketplace consumer key |
| `mqtt_password` | if your broker needs one | Home Assistant sensors |

Check what is in place (this never prints a value):

    docker compose exec api python -m app.secretsctl check

Order of lookup: the key vault (if configured), then the file, then the upper-cased environment variable.

## Using a key vault

Set `WA_SECRETS_BACKEND` in `.env` (for Vault/OpenBao also add `COMPOSE_FILE=docker-compose.yml:docker-compose.vault.yml`, which mounts the sign-in files), then recreate the containers (`docker compose up -d`).

The database container reads `postgres_password` straight from its file, so keep that one as a file even
when you use a vault for the rest. Compose also needs every file in the secrets directory to exist, even
empty, which `init-secrets.sh` takes care of.

### HashiCorp Vault or OpenBao (`WA_SECRETS_BACKEND=vault`)

Store all the secrets as fields of one KV version 2 document, so one request fetches everything:

    vault kv put secret/world-assistant api_token=... tfl_app_key=... nr_access_token=...

Settings in `.env`: `WA_VAULT_ADDR` (required), `WA_VAULT_MOUNT` (default `secret`), `WA_VAULT_PATH`
(default `world-assistant`), `WA_VAULT_NAMESPACE` and `WA_VAULT_CA_FILE` (both optional).

Sign-in credentials are the only secrets that stay in files (or the environment), because the vault cannot
hold its own key:
- a token in `vault_token.txt`, or
- AppRole: `vault_role_id.txt` and `vault_secret_id.txt` (`WA_VAULT_APPROLE_MOUNT` if not `approle`).
  Give the role read access to that one path only.

### Any other vault (`WA_SECRETS_BACKEND=command`)

Set `WA_SECRETS_COMMAND` to a command that prints one secret; `{name}` is replaced by the secret name.
No shell is used. For example:

| Vault | Command |
|---|---|
| 1Password | `op read op://Home/world-assistant/{name}` |
| Bitwarden | `bw get password {name}` |
| Azure Key Vault | `az keyvault secret show --vault-name myvault --name {name} --query value -o tsv` |
| AWS Secrets Manager | `aws secretsmanager get-secret-value --secret-id wa/{name} --query SecretString --output text` |
| Google Secret Manager | `gcloud secrets versions access latest --secret=wa-{name}` |

The command must exist inside the container. The stock image does not include these tools, so either build
a small derived image that adds the one you use, or use Vault/OpenBao, which needs nothing extra. Azure
and AWS names cannot contain underscores in some cases; use a wrapper script that maps the name.

## Behaviour you can rely on

- Values are cached for `WA_SECRETS_CACHE_SECONDS` (default 300), so rotating a secret in the vault takes
  effect within five minutes with no restart.
- If the vault cannot be reached, the last values it returned are used for up to an hour, then the files.
  A vault outage does not stop the service.
- A secret the vault does not have falls back to the file, so you can move keys across one at a time.
- Values are never logged or returned by any endpoint or by `secretsctl`.

## The bundled OpenBao

Don't have a vault? `bash scripts/openbao.sh setup` runs OpenBao beside the app and moves your secrets into
it. See docs/openbao.md.
