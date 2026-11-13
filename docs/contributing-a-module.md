# Contributing a module

A **module** is one area of World Assistant: fuel prices, the bus tracker, flood warnings, the bike garage. Each one
can be switched on or off in Settings → Modules without a restart ([modules](modules.md)). This page is for
someone who wants to add one.

There are two ways:

| | **A built-in module** (a pull request here) | **An outside module** (your own package) |
|---|---|---|
| Ships in the signed release | Yes | No: you build your own image |
| Pages, endpoints, scheduled jobs | Yes | Yes |
| Keys, database tables, assistant tools, Home Assistant sensors, a tab in the menu | Yes | Not yet (see [what an outside module cannot do yet](#what-an-outside-module-cannot-do-yet)) |
| Automatic updates | Yes | No: a home-built image is not signed, so the updater will not install it |
| Who maintains it | Reviewed and kept working with the rest | You |

**For now, a pull request is the way to contribute.** An outside package suits trying an idea out on your own server.

## Before you start

Open an issue first, saying what the module does, where its data comes from and the source's terms. A few things
decide whether it can be included:

- **The source allows it.** A public API, open data, or your own account's data. Check the licence and terms of
  use. If a site publishes no API, read its robots.txt and terms first, and never disguise the client as a
  browser.
- **It is polite.** It keeps within the source's limits through the shared rate limiter, and caches answers.
- **It is useful to someone else.** It covers a whole area or a common need, not one person's setup.
- **It never acts on anyone's behalf** in a way that costs money or cannot be undone: no ordering, booking or
  paying. It can prepare things for a person to finish.

## A built-in module, step by step

The bike garage (`app/modules/bikes/`, about 6,000 lines) and crime (`app/modules/crime/crime.py`, small) are good examples to read
alongside this.

### 1. The code

Everything the module is goes in its own folder, `app/modules/<id>/`: its API, pages, jobs, assistant tools and
notifications, with each outside service it reads under `sources/<name>/`. It may use only the core's published
interface (the [module SDK](module-sdk.md)), its own folder, and the modules it declares in `needs` or `uses`;
`tests/test_module_boundaries.py` checks every import. Every outside service has the same shape:

| File | What it holds |
|---|---|
| `client.py` | The HTTP calls: auth, paging, retries, and the rate limiter (`app/ratelimit.py`) |
| `normalise.py` | **Pure** functions from raw JSON to dataclasses. They are tested against small, hand-written fixtures |
| `store.py` | The SQL, if anything is kept |
| `sync.py` | The worker jobs |
| `probe.py` | `python -m app.modules.<id>.sources.<name>.probe` prints a real response, for when the source changes |

The feature itself (its API, pages and rules) goes beside them in the folder, and `README.md` there is written
from the declaration and the files by `python scripts/module-readmes.py` (a test checks it is current).

### 2. The declaration

Add one `Module(...)` to `BUILTIN` in `app/modules/builtin.py`. The core reads it and wires everything up. A
module never edits the core's own lists.

```python
Module("crime", "Crime and police",
       "Street-level crime within a mile of a place and its neighbourhood police team",   # what it gives you
       category="place", link="https://data.police.uk/",
       note="England, Wales and Northern Ireland; the figures are about two months behind.",
       routers=("app.modules.crime.crime:router",),          # import strings: loaded only when used
       prefixes=("/v1/crime",),                # these answer "switched off" when the module is off
       tools=("crime",),                       # its assistant tools, by name
       uses=("codepoint",),                    # other modules it uses when they are there
       provides={"assistant.tools": "app.modules.crime.tools:TOOLS"}),   # where it plugs into the core
```

The other fields:

| Field | What it declares |
|---|---|
| `keys`, `more_keys` | The secrets it needs (`key_optional=True` when it works without, better with; `any_key=True` when one will do) |
| `worker` | Its scheduled jobs: `Job("app.x.sync:poll", {"minute": {0, 30}, "timeout": 300})` |
| `jobs` | The names its jobs record runs under, so Settings can say when it last worked |
| `switch` | A setting that turns its polling on, for heavy downloads that start off |
| `needs` | Other modules it cannot work without; they are switched on with it |
| `uses` | Other modules whose code it uses when they are there; it must cope without them |
| `provides` | Where it plugs into the core, by extension point ([module SDK](module-sdk.md#extension-points)) |
| `services` | Optional containers it uses, by compose file (`docker-compose.graph.yml`) |
| `coverage`, `area` | Where it works (`("london",)`, or a box), so it is suggested to people who live there |
| `category`, `note`, `listed` | How it is shown in Settings |

`tests/test_modules.py` checks every declaration against the code. Every import must resolve, every key must be
known and reach the containers, every route must sit under its prefixes, every tool must exist, and every
schedule must be one arq accepts.

### 3. Endpoints

- An `APIRouter` with `prefix="/v1/<name>"` and `dependencies=[Depends(require_token)]`. The token rules
  (none on the home network, a token from outside) then apply by themselves ([security](security.md#access)).
- **Read from the database where you can.** A question should not wait on a slow or rate-limited source. If you
  must ask live, cache the answer and say so when the source is down.
- A docstring on each endpoint. Its first paragraph becomes its line in the [API reference](api.md); run
  `python scripts/api-doc.py` after changing an endpoint (a test checks it).

### 4. Pages

- A page is a static shell that **holds no data**: its script calls your `/v1` endpoints. Serve it from a router
  with `include_in_schema=False` and pass it through `pagestyle.themed(PAGE, "<id>", request)`, which adds the
  menu, the theme and the token box.
- **Use theme tokens, never colours** (`var(--accent)`, `var(--good)`), so every theme works in light and dark.
  `tests/test_themes.py` enforces it ([themes](themes.md)).
- A tab in the menu goes in `PAGES` in `app/pagestyle.py`; a sub-tab under an existing page goes in `SECTIONS`.
- It must work on a phone. Check it at 390 px wide.

### 5. Scheduled jobs

- A job is an async function taking the worker's context (`ctx["engine"]` and the rest). Declare it in `worker=`.
- **Replace only on complete data.** A fetch that failed part-way must never clear what is stored
  ([ADR 0002](adr/0002-polling-and-completeness.md)).
- **Running twice must be harmless.** During an update the old and new workers overlap briefly. Scheduled runs
  are kept to one at a time, but a job started by hand may meet one already running.

### 6. Keys

A new key needs to be added in four places:

1. a `Known(...)` entry in `app/secretstore.py` (add it to `WEB_KEYS` if people may paste it in Settings);
2. the `x-app` secrets list and the `secrets:` section of `docker-compose.yml`;
3. `scripts/init-secrets.sh`, which makes its empty file;
4. a row in [data sources](data-sources.md): where to get it, the limits, the licence.

A key is never logged, never returned by the API, and never put in an environment variable or a URL if the
source accepts a header.

### 7. The database

Add an Alembic revision in `migrations/versions/`. **Migrations only add:** new tables, and new columns that are
nullable or have a default. To remove or rename something, use two releases ([database changes](migrations.md)).
Anything with a position gets a `geog` column and a GiST index ([ADR 0007](adr/0007-postgis.md)).

### 8. Joining up with the rest

All optional, and all declared in the code you add, not in the core:

- **The assistant:** a `TOOLS` list of `Tool(...)` in the module's `tools.py` (name, description, JSON schema,
  function), declared as `provides={"assistant.tools": "app.modules.<id>.tools:TOOLS"}` with the names in `tools`.
  Write the description for an AI assistant: what it answers and what to give it.
- **Home Assistant:** a device function in `app/ha_publish.py` that turns your data into sensors, called from
  `ha_sync` ([Home Assistant](home-assistant.md)).
- **Notifications:** in the module's `notify.py`, `await notify.send(engine, kind, key, title, body)`, where `key`
  makes it send only once (`notify_jobs.send_many` puts several in one message). A
  new kind goes in `KINDS` in `app/notify.py`, off by default unless people would clearly want it
  ([notifications](notifications.md)).
- **The map, Here and the graph:** each has a layer or part list to add to (`app/map_layers.py`, `app/modules/here/here.py`,
  `app/modules/graph/build.py`).

### 9. Tests

- **Tests come with the code.** Parsers are tested with small, hand-written fixtures. Never commit a real
  response (`samples/` is ignored).
- **HTTP is faked** with `httpx.MockTransport`, so tests never reach the internet.
- **Database tests** use `WA_TEST_DATABASE_URL` and are skipped without it.
- **No personal data:** use central London (51.5074, -0.1278), never a real home, postcode or address.
- CI also starts the whole stack, requests every page and endpoint, and updates the previous release to yours in
  stages. That catches a page that errors, or a migration that breaks the running version.

### 10. Documentation

- a page `docs/<name>.md`: what it does, where the data comes from, its keys and settings, its endpoints;
- a line in the [README](../README.md) under the right area, and in the [docs index](README.md);
- a row in [data sources](data-sources.md);
- an entry under `## [Unreleased]` in the [changelog](../CHANGELOG.md).

Write plainly, for someone who has not seen the code: short sentences, and what it does for you before how it works.

### 11. The pull request

Run `ruff check .` and `pytest` first. CI must pass: lint, tests, image build, secret scan, dependency review,
the whole-stack check and the staged update. A maintainer reviews it, and it ships with the next release. Every
module can be switched off, so a new one never changes anything for someone who does not want it.

## An outside module

Your own Python package can declare modules, and World Assistant finds them when it starts:

```toml
# pyproject.toml of your package
[project.entry-points."worldassistant.modules"]
tides = "wa_tides.module:MODULE"
```

```python
# wa_tides/module.py
from app.modules.model import Job, Module

MODULE = Module("tides", "Tide times", "High and low water at the coast near your places",
                routers=("wa_tides.api:router",), prefixes=("/v1/tides",),
                worker=(Job("wa_tides.sync:poll", {"minute": {5}, "timeout": 120}),), jobs=("tides_poll",))
```

You can also name it without packaging it: `WA_EXTRA_MODULES=wa_tides.module:MODULE` in `.env` (the package must be
importable). The rules:

- **Restart once** for it to be found.
- **It cannot take a built-in's id.**
- **It can always be switched off** in Settings → Modules.
- **If it fails to load, it is logged and skipped.** It can never stop World Assistant starting.

To run it, build your own image on top of a release:

```dockerfile
FROM ghcr.io/rpearson-dot/world-assistant:0.51.0
COPY wa_tides /opt/wa_tides
RUN pip install --no-deps /opt/wa_tides
```

Then set `WA_IMAGE` to your image. Your image is not signed by this repository, so **automatic updates and Update
now will not install releases over it**: you rebuild it on each new release yourself.

### What an outside module can and cannot do yet

An outside module uses the same [module SDK](module-sdk.md) as a built-in one, so its pages, endpoints, scheduled
jobs and assistant tools (`provides={"assistant.tools": ...}`) work as theirs do. What it still cannot add:

- **keys**, which are listed in `app/secretstore.py` and the compose files;
- **database tables** through the core's migrations (it can keep its own schema);
- **menu tabs** and Home Assistant sensors, which are listed in the core.

Those gaps, and a trusted way to install signed outside modules, are on the [roadmap](roadmap.md). Until then,
contribute through a pull request.
