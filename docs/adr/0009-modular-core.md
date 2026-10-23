# 0009 — A small core, and everything else a module

**Status:** accepted, 2026-10-23

## Context
World Assistant grew by adding categories (fuel, buses, energy, floods, events, pollen, boundaries). Each one
followed ADR 0008, but adding one still meant editing about seven places by hand: the router list, the worker's
job and cron lists, the assistant's tools, the Setup list, the secret store, the compose secrets and the settings.
The product is now meant to be self-hosted by many people, each wanting a different set of sources, with a key
for some and not others, and later with modules written by other people.

## Decision
**A module is one declaration** (`app/modules/model.py`): its id and name, the keys it needs, the optional
containers it uses, the routes, worker jobs (with schedules) and assistant tools it adds, the URL prefixes that
go dark when it is off, the modules it depends on, and the region it covers. The built-ins are declared in
`app/modules/builtin.py`. The core reads the declarations and wires everything; a module never edits the core.

**The core** is what always runs: the web, API and assistant (MCP) server, authentication, the secret store,
settings, places, status, Home Assistant publishing, backups and updates, and the module registry. It cannot be
switched off.

**Enable on demand, in one image.** Every built-in module ships in the same signed image (ADR 0004). Switching a
module on or off is a saved choice, read with a 15 second cache, so it needs no restart:
- the web answers 404 under a switched-off module's prefixes (a page explains and links to Modules);
- the worker wraps each job so it returns at once while its module is off;
- the assistant neither lists nor runs the tools of a module that is off.
With no choice saved, everything is on, so an upgrade changes nothing. A user can start from "the essentials
only" and add what they want. A module's dependencies are switched on with it.

**Modules from outside the image are possible, and nothing here prevents them.** Declarations are found through
the Python entry-point group `worldassistant.modules` and `WA_EXTRA_MODULES` (import strings). Everything that
runs is named by an import string and imported only when used, so an outside module costs nothing while off.
An outside module cannot take a built-in's id and is never core, so it can always be switched off. One that fails
to load is logged and skipped. What is deliberately *not* done yet: fetching or installing code at run time. That
needs a trust decision (who signs a third-party module, what it may touch) and should be its own ADR; the registry
is the place it would plug in.

**Keys: one lookup, a choice of where they live** (already true; now offered at setup). Files in the secrets
folder (default), the bundled OpenBao, a Vault or OpenBao the user already runs, or any key manager with a command
line (1Password, Bitwarden, pass, sops, the cloud providers). `scripts/setup.sh` asks; the Modules page shows
what is in use and how to change it. Changing it stays a setting in `.env`, because the app must not rewrite its
own way of reading secrets.

**One migration chain.** Every table exists whether or not its module is on. Empty tables cost nothing, and
per-module migration branches would make upgrades fragile.

**Where you live suggests what to turn on.** A module can declare the region it covers (a box and region names).
The Modules page marks those that cover the home place, using the loaded Boundary-Line regions when there are some.

## Consequences
- Adding a built-in feature is one entry in `builtin.py` plus its code. Tests check that each declaration
  matches the code (every import resolves, every key is a known secret and reaches the containers, every route is
  under its prefixes, every assistant tool is owned, every schedule is valid).
- A switched-off module's data stays in the database; switching it back on loses nothing.
- Hiding a module is not a security boundary: the choice is for convenience and quiet, and the token still
  protects everything.
- Routes are mounted for every module regardless of the choice (only gated), so enabling needs no restart. A
  module added by an outside package needs one restart to be discovered.
