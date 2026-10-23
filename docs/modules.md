# Modules

World Assistant is a small core plus modules. The core is the web, API and assistant (MCP) server, sign-in, the
key store, settings, places, status, Home Assistant publishing, backups and updates. Everything else (fuel prices,
the bus tracker, flood warnings, events, pollen, energy, boundaries, the graph) is a module you can switch on or
off. Why it is built this way: [ADR 0009](adr/0009-modular-core.md).

## Choosing what is on

Open **Modules** (`/modules`, also linked from Settings).

- Nothing is chosen on an install from before modules existed, so everything is on. Press **Start with the
  essentials only** to turn the lot off, then switch on what you want. **Turn everything on** goes back.
- A switch takes effect within seconds, with no restart. A switched-off module's pages answer "switched off", its
  scheduled jobs do nothing and its assistant tools disappear. Its data stays, so switching it back on loses nothing.
- A module that needs a key says which. Put keys under **Settings > Keys**, then **Test key** where offered (BODS,
  TfL and TfWM have a live test; for the others the Setup list shows whether they are working).
- Modules that cover only part of Britain are marked **suggested** when they cover your home place (London for
  TfL, the West Midlands for TfWM). Boundary-Line regions are used when loaded, otherwise a rough box.

The same from the API (token needed): `GET /v1/modules`, `PUT /v1/modules {"mode": "only", "on": ["bus"]}`,
`POST /v1/modules/{id}/enable`, `POST /v1/modules/{id}/disable`, `POST /v1/modules/{id}/test`, `DELETE /v1/modules`.

## Where your keys are kept

Set up by `scripts/setup.sh`, and shown on the Modules page: files in the secrets folder (default), the bundled
OpenBao (`bash scripts/openbao.sh setup`), a Vault or OpenBao you already run, or your own key manager through a
command (1Password, Bitwarden, pass, sops, AWS, Azure, Google). Details: [secrets](secrets.md).

## Adding a module (for developers)

A module is one `Module(...)` declaration (see `app/modules/model.py` and the built-ins in
`app/modules/builtin.py`): keys, routes, worker jobs and schedules, assistant tools, URL prefixes, dependencies and
coverage. Add it to `BUILTIN` and nothing else in the core changes. `tests/test_modules.py` checks the declaration
against the code.

### From outside the image

Declare a `Module` (or a list, or a function returning them) in your own Python package and either

- register an entry point in the group `worldassistant.modules`, or
- name it in `WA_EXTRA_MODULES` as `package.module:NAME` (the package must be importable).

It is found when the API and worker start. It cannot reuse a built-in's id, it can always be switched off, and if it
fails to load it is logged and skipped. Installing such a package into the image is not automated yet.
