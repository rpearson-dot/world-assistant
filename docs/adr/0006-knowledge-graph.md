# 0006 A knowledge graph the assistant can walk

**Status:** accepted

**Context.** The assistant answers each question from a purpose-built endpoint. Questions that span
datasets — "what is on the way to work, and is anything blocking it?" — need a new endpoint each
time, because nothing records how the datasets relate to one another. Those relationships do exist
(a trip runs between places, uses a corridor; a closure falls on a corridor; a station sits near a
place) but only one of them, `nh_event_corridor`, is written down.

Three ways to hold them were weighed: an edge table in Postgres queried with recursive CTEs,
Apache AGE (openCypher inside Postgres), and a graph database beside Postgres. Between Neo4j and
Memgraph: Memgraph is faster and lighter, but it is in-memory first, which is a poor fit on a NAS
where RAM is shared, and its Business Source Licence is source-available rather than open source.
Neo4j Community is GPLv3, and the tooling for feeding a graph to a language model mostly assumes it.

At this size — thousands of nodes — none of the three would be too slow. The engine is not the
constraint; having the relationships written down at all is.

**Decision.** Neo4j Community beside Postgres, in an optional compose file, holding relationships
derived from Postgres.

The graph owns nothing. Every node is a label, an id that points back at a Postgres record, and the
few properties a traversal needs to make a decision or draw a label. A job rebuilds the whole graph
from one read of Postgres, stamps everything with that build's id, and deletes whatever the previous
build left. Nothing is ever written back to Postgres, and nothing is written to Neo4j from anywhere
else.

History and configuration stay in Postgres. Time-series answered by aggregates — the bus record, fuel
price changes, pollen readings — are what a graph database is worst at, and what Postgres is best at.

**Consequences.** Losing Neo4j costs nothing: delete the volume, run the build, it is back. It is
therefore left out of the backup, and `pg_dump` still covers everything that matters. There are no
dual writes, so the two stores cannot disagree; the cost is that the graph is as fresh as the last
build, which runs twice an hour.

It is a fourth datastore, and a fourth thing to keep patched. If it is not earning its place, it can
be removed by deleting a compose file, a job and a router — no migration, and no data goes with it.

Proximity relationships (`NEAR`, `ALONG`) are computed in Python over every pair, because nothing in
WA uses PostGIS yet even though the extension is installed. That is fine at this size and is the
obvious thing to replace when PostGIS is turned on properly.
