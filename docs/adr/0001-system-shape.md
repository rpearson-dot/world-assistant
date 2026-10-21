# 0001 Shape of the system

**Status:** accepted

**Context.** The data sources have tight rate limits and are slow. Questions ("how is the trip to
Brighton?") should answer instantly and keep working when an upstream API is down.

**Decision.** A worker polls feeds on a schedule and writes to Postgres; an API answers from
Postgres. FastAPI for HTTP, arq (Redis) for scheduling, Alembic for schema, PostGIS Postgres for
storage. Reuse the existing Redis and MQTT broker rather than running new ones.

**Consequences.** Answers are as fresh as the last poll. Only the worker talks to rate-limited
feeds, which makes the limit easy to honour. History accumulates as a by-product. More moving
parts than a single script, but each is small and replaceable.
