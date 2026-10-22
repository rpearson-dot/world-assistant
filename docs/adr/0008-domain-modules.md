# 0008 — Domain modules, and World Assistant reports while Home Assistant acts

**Status:** accepted, 2026-10-22

## Context
New categories (energy supply first) keep arriving. Each needs a client for an outside service, some logic, storage, a job, API routes and Home Assistant sensors.

## Decision
A category is a self-contained package (`app/energy/` is the model): `client` for the outside service, pure `rates`-style logic with no I/O (so it is tested alone), a `store`, a `sync` job with a "disabled" / "not configured" short-circuit, `routes`, and an `ha` module that builds a Device. Wiring is a handful of one-line registrations (router, worker cron, `ha_publish.collect`, secret, config flag). Each has its own on/off switch and skips itself when unconfigured.

World Assistant is the outside world: it reports. Anything that changes the house (the battery inverter included) is done by Home Assistant reading WA's sensors. WA holds no credentials for, and opens no connection to, a device it would control.

## Consequences
New domains are cheap to add and to remove. Control logic lives where the user can see and edit it (HA automations).
