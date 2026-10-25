# The knowledge graph

Neo4j holds how World Assistant's data joins up, so a question can be answered by following
connections rather than by knowing in advance which endpoint to call.

It holds **no data of its own**. Every node carries the id of the Postgres record it stands for, and
a job rebuilds the lot from Postgres. Drop it, rebuild it, nothing is lost. It is deliberately left
out of the backup; `pg_dump` already covers everything that matters. Why it is built this way:
[ADR 0006](adr/0006-knowledge-graph.md).

## Turning it on

```
docker compose -f docker-compose.yml -f docker-compose.graph.yml up -d
```

`scripts/init-secrets.sh` makes the `graph_password` secret (and the `neo4j_auth.txt` the Neo4j image
wants) if they are not there already. Set `WA_GRAPH_ENABLED=true` so the api and worker use it. The
service is reachable only inside the stack, as `bolt://wa-graph:7687`; nothing is published to your
LAN. To look around with Neo4j Browser, publish port 7474 for as long as you need it.

With it switched off, every graph endpoint answers 503 and says so. Nothing else changes.

## What is in it

| Node | One per | Carries |
| --- | --- | --- |
| `Place` | saved place | name, kind, postcode, position |
| `Trip` | trip | name, whether it is enabled |
| `Corridor` | road corridor | name, road numbers, width |
| `RoadEvent` | open closure or speed limit | kind, cause, limit |
| `Station` | fuel station | name, brand, position |
| `Location` | Home Assistant location | name, position |
| `Vehicle` | vehicle | name, kind |
| `BusTracker` | bus tracker | line |
| `BusStop` | stop a bus tracker watches | name, indicator, position (from NaPTAN) |
| `FloodWarning` | live flood alert, warning or severe warning | area, level, severity, county, river or sea |
| `Area` | region, county or district (kinds from `WA_BOUNDARY_KINDS`) that something is in | name, kind, code |
| `Disruption` | live TfL road disruption (works, incidents, closures) | location, category, severity, planned or not |
| `LocalEvent` | event in the next fortnight from Skiddle or Ticketmaster | name, day, venue, town, type |

| Relationship | Meaning |
| --- | --- |
| `(Trip)-[:FROM\|:TO]->(Place)` | where a trip runs between |
| `(Trip)-[:USES]->(Corridor)` | the corridor built for that trip |
| `(RoadEvent)-[:AFFECTS]->(Corridor)` | a closure or limit on that corridor |
| `(Station)-[:ALONG {km}]->(Corridor)` | a station close enough to be on the way |
| `(Station\|Location\|BusStop)-[:NEAR {km}]->(Place)` | close to a place, with the distance |
| `(Vehicle)-[:DRIVES]->(Trip)` | a vehicle that could make the trip |
| `(BusTracker)-[:CALLS_AT]->(BusStop)` | a stop on a tracker's route |
| `(FloodWarning)-[:COVERS]->(Place)` | the warned area contains the place |
| `(FloodWarning)-[:CROSSES]->(Corridor)` | the warned area crosses the corridor |
| `(Disruption)-[:AFFECTS]->(Corridor)` | a TfL disruption lying along that corridor |
| `(Disruption\|LocalEvent)-[:NEAR {km}]->(Place)` | close to a place |
| `(Place\|BusStop\|RailStation\|Disruption\|LocalEvent\|Station\|Location\|PowerCut\|StreetWork\|FloodWarning)-[:IN]->(Area)` | inside that area (a flood warning: its area reaches into it) |
| `(Area)-[:IN]->(Area)` | a district in its county, a county in its region |

Only areas something is in are written, plus the wider areas above them, so the graph does not carry every
district in Britain. Boundaries need the OS Boundary-Line load ([boundaries](boundaries.md)); events need the
events sync (`WA_EVENTS_SYNC_ENABLED`, a Skiddle or Ticketmaster key; it reads around up to
`WA_EVENTS_SYNC_PLACES`, 20, places, `WA_EVENTS_SYNC_PAGES`, 3, pages of 100 from each source at each). Only fuel
stations near a place or along a corridor are put in areas: all of Britain's would bury them.

The Overview shows up to 12 links for each place, trip, vehicle and tracker (its areas, routes and supply first,
then what is nearest), so a busy place does not crowd the others out.

`WA_GRAPH_NEAR_KM` (3 km) and `WA_GRAPH_ALONG_KM` (2 km) decide how close counts.

## Asking it

| Endpoint | Answers |
| --- | --- |
| `GET /v1/graph/about` | the vocabulary; works even with the graph switched off |
| `GET /v1/graph/counts` | how many of each node and relationship there are |
| `GET /v1/graph/neighbours?id=home` | everything joined to one thing, either direction |
| `GET /v1/graph/disruption?place=home` | closures and limits on the corridors of trips from a place |
| `POST /v1/graph/rebuild` | rebuild now rather than waiting |

`disruption` is the one that shows the point: it follows place → trip → corridor → road event, four
steps, without anyone writing a query for that question. It also reports flood warnings that cover the
place itself or cross the corridor of one of its trips.

## Rebuilding

The worker rebuilds at **:08 and :38** past each hour, so the graph is never more than half an hour
behind Postgres. By hand:

```
docker compose exec worker python -m app.graph.build
```

Each build stamps every node and relationship with its own id and then deletes anything carrying an
older one, so a rebuild replaces rather than accumulates. Interrupting a build leaves the previous
graph in place.

## What it will not hold

History and configuration stay in Postgres. The bus record, fuel price changes and pollen readings
are time-series answered by aggregates — the shape a graph database is worst at. Places, trips and
vehicles rely on foreign keys, unique constraints and Alembic migrations, which Postgres does better.

## A caveat worth knowing

There is no Neo4j in CI, so the tests drive the writer against a fake session: they prove the order,
the grouping and the parameters, but not that the Cypher parses. The first real `graph_build` is what
proves that. If it fails, the error names the statement.

Proximity (`NEAR`, `ALONG`) is measured by PostGIS during the build, so the distances on those
relationships are spheroid distances from the same index the rest of WA now uses.

## The Graph page

`/graph` draws the graph as nodes and edges: search for a place, trip, road or station; pick a ready-made
question (everything around home, what is on my trips' roads, fuel stations along my routes, flood warnings,
bus trackers); tap a node for its details and double-tap (or Expand) to bring in what it connects to. Under
**Ask with Cypher** you can write your own read-only query: return nodes, relationships or paths to see them
drawn, or values for a table. Anything that would change the graph is refused, and the session is read-only.


### Customise the view

Under **Customise the view** on the page: tick which kinds of thing and which links to show, choose a colour for
each kind, size nodes by how connected they are, show names always, only when zoomed in, or never, hide the link
names, and set node size and spread. Hiding a kind only hides it; it stays loaded, so ticking it again brings it
back. The choices are kept in this browser (not on the server); **Reset the view** clears them.
