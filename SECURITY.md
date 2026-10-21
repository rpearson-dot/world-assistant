# Security policy

## Reporting a vulnerability

Please report privately using GitHub's "Report a vulnerability" on the Security tab of
this repository. Do not open a public issue for anything that could be exploited.
This is a personal project: expect an acknowledgement within a few days, not a service
level agreement.

## Supported versions

Only the latest release is supported.

## What not to put in issues or commits

API keys, tokens, passwords, home addresses or coordinates. Secrets live in files outside
the repository (see `scripts/init-secrets.sh`); `gitleaks` runs on every push. If you
think a secret has been committed, rotate it first and then tell me.
