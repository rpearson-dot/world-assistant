# GitHub repository settings

The settings that make the repository safe to run in public, and the reasons. Most are in the GitHub
web page, not in the code, so this is the checklist to reproduce them (for example if you fork it).

## Branch ruleset for `main`

Settings > Rules > Rulesets > New ruleset > New branch ruleset. Name it `main`, enforcement **Active**,
target: **Include default branch**. Leave the bypass list empty.

| Rule | Setting | Why |
|------|---------|-----|
| Restrict deletions | on | `main` cannot be deleted |
| Block force pushes | on | history cannot be rewritten |
| Require a pull request before merging | on, **0** required approvals, allowed merge method **Squash** | every change is reviewed in a pull request and CI runs on it; the owner cannot approve their own pull request, so approvals stay at 0 |
| Require status checks to pass | on: `test`, `secrets`, `image`, `stack`; do **not** require branches to be up to date | nothing merges unless the full CI (including the whole-stack check) is green. `dependency-review` runs only on pull requests, so it is not listed |

The checks only appear in the picker after they have run once, so create the ruleset after the first CI
run on the repository.

What this means in practice:

- Nobody, including the owner, can push straight to `main`. Work goes on a branch, into a pull request,
  and is merged when CI is green.
- Automatic updates still work: they merge Dependabot pull requests through the pull request API, which
  honours the same rules (so only a green pull request merges).
- Releases are unaffected: the release workflow creates a tag and a release, it does not push to `main`.
  Do not add a tag ruleset without allowing the workflow to bypass it, or releases will fail.
- In an emergency, set the ruleset to **Disabled**, fix, and set it back to **Active**.

## Actions

Settings > Actions > General:

- **Fork pull request workflows:** require approval for all outside collaborators.
- **Workflow permissions:** read repository contents by default (each workflow asks for more where it
  needs it).
- **Allow GitHub Actions to create and approve pull requests:** off.

## Security features

Settings > Advanced Security: turn on **private vulnerability reporting**, **Dependabot alerts**,
**Dependabot security updates**, **secret scanning** and **push protection**.

## Variables (Settings > Secrets and variables > Actions > Variables)

| Variable | Value | Effect |
|----------|-------|--------|
| `AUTO_UPDATES` | `true` | daily Dependabot checks merge safe updates once CI is green ([runbook](runbook.md)) |
| `ENABLE_CODEQL` | `true` | CodeQL code scanning (free on public repositories) |
| `ENABLE_GH_ATTESTATIONS` | `true` | GitHub build-provenance attestation (public repositories) |
| `AUTO_RELEASE` | leave unset | would let the update workflow cut releases by itself |

## Packages

The image is published to `ghcr.io/<owner>/world-assistant`. It is private until you open the package
(your profile > Packages > the package > Package settings > Change visibility). Making it public means
servers need no `docker login` to pull it. Releases are signed either way.

## Account

Use two-factor authentication on the owning account, and keep write access to as few people as possible:
anyone who can merge to `main` and run the release workflow can change what a server with automatic
updates installs ([security](security.md)).
