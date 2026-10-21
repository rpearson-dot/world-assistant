# 0005 Self-hosted routing with Valhalla

**Status:** accepted

**Context.** Trips need a real road line and the road numbers used, so corridors follow the route
and events can be filtered by road. Routes are computed only when a trip is added or changed.

**Decision.** Run Valhalla in the stack with a Great Britain extract. It gives geometry, road
references per manoeuvre, and a base journey time. It is not published outside the stack.

**Consequences.** No per-request limits or third-party terms for routing, at the cost of about 2 GB
for the map, a few GB of disk for tiles, and a slow first build. The journey time does not include
live traffic; that needs a separate provider. The container image used today comes from an archived
project and should move to the upstream image.
