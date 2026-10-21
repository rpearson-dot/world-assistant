# Security

This runs on a home network. The aim is: nothing on it is reachable without a token, no secret is
ever in the repository or the image, and what runs is exactly what CI built.

## Access

- `/v1/*` requires `Authorization: Bearer <token>`; the token is compared in constant time.
  If no token is configured the API refuses everything (503) rather than running open.
- The pages (`/status`, `/places`, `/fuel`, `/events`, `/pollen`, `/bus`, `/trains`) are static shells that hold no data. They ask for the
  token and keep it in the browser's `sessionStorage` for the session.
- There is no TLS in the app. Reach it over a private network (for example Tailscale) or put a
  reverse proxy with TLS in front. Do not expose the port to the internet.

## Secrets

- Secrets are files (`/run/secrets/*`) mounted from a directory outside the project folder. They
  are not environment variables, so they do not show up in `docker inspect` or process listings.
- `scripts/init-secrets.sh` generates the database password and API token. Upstream keys are
  entered by hand.
- After changing a secret file, recreate the containers so they see the new file.
- All secrets are looked up in one place (`app/secretstore.py`), and can optionally come from a key
  vault instead. See [secrets.md](secrets.md). `python -m app.secretsctl check` shows where each one
  comes from and never prints a value.
- Never paste keys into chat, issues or commits. `.gitignore` excludes `secrets/` and `.env`, and
  gitleaks scans every push.

## Containers

- The image is built in two stages; the final image holds only the virtualenv and the app.
- Runs as a non-root user (uid 10001) with no shell. `docker-compose.hardened.yml` adds a read-only
  root filesystem, a small `noexec` tmpfs for `/tmp`, all capabilities dropped, no privilege
  escalation, and a process limit.
- Valhalla publishes no port; only stack containers can reach it.

## Supply chain

| Control | Where |
|---------|-------|
| Dependencies pinned with hashes; install refuses anything else | `requirements.lock`, `pip --require-hashes` |
| Base image pinned to an exact digest per release, recorded in provenance | `release.yml` |
| Actions pinned to commit SHAs | all workflows |
| SBOMs in SPDX and CycloneDX, attached to the image and the GitHub release | `release.yml` |
| Keyless image signature and attestations (Sigstore) | `release.yml` |
| Build provenance attestation | `release.yml` |
| Image vulnerability scan on release and weekly | Trivy |
| Secret scanning on every push | gitleaks |
| Code scanning (CodeQL) and dependency review | CodeQL is off until you set the repository variable `ENABLE_CODEQL=true`; GitHub offers it free on public repositories. Dependency review runs on every pull request. |
| GitHub build-provenance attestation | Off until you set the repository variable `ENABLE_GH_ATTESTATIONS=true`; GitHub only stores it for public repositories (or paid plans). The cosign signature and SBOM attestations are produced either way. |
| Release gate | A release is refused unless the commit is the tip of `main`, the version is new and CI (`test`, `secrets`, `image`, `stack`) has passed on it. No override. |
| Automated update PRs | Dependabot, daily. Optionally merged automatically when safe (see below). |
| Whole-stack check | CI starts the real stack on PostGIS and requests every page and endpoint on every push, and the same read-only check runs after every deploy. |

Verify a release before deploying it. Each release's notes contain the exact command for that image; it
looks like this (use the repository name exactly as it appears there; it is case-sensitive, and the automatic
update job matches it case-insensitively):

```bash
cosign verify ghcr.io/rpearson-dot/world-assistant@sha256:<digest> \
  --certificate-identity-regexp 'https://github.com/rpearson-dot/world-assistant/.github/workflows/release.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Automatic updates: what is trusted

Both are opt-in and off by default.

- **GitHub side (`AUTO_UPDATES`):** the workflow merges only Dependabot pull requests that have passed CI and
  are safe: patch updates of Python libraries and container images, and minor updates of Python libraries.
  Never a major version, never GitHub Actions (the built-in token cannot change workflows), never a database
  image. It cannot release unless you also set `AUTO_RELEASE`.
- **Server side (the switch on `/status`):** the api cannot restart containers; the switch is a stored flag.
  A nightly job on the server installs only a **release image whose signature verifies against this
  repository's release workflow**; otherwise it refuses. It takes a backup first, and puts the previous
  version back if the post-update check fails. Its GitHub token is read-only (Contents: read) and lives in
  a file outside the project folder, handed to git through the environment so it is not in process lists or
  `.git/config`. Anyone who can push to `main` and run the release workflow can change what your server
  installs, so keep two-factor authentication on the GitHub account and review who has write access.

## Known gaps

- Redis is part of the stack, has no published port and needs the `redis_password` secret. A Redis you
  point `WA_REDIS_URL` at yourself is only as safe as you make it.
- One shared API token, no per-user accounts or scopes.
- The database holds your places (including home). Back it up somewhere private and treat dumps
  as sensitive. Never put real coordinates or addresses in issues, pull requests or test fixtures.
- The dependency lock covers x86-64 Linux wheels only.
