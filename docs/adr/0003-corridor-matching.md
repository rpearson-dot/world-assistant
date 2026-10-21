# 0003 Corridor matching in plain Python

**Status:** accepted

**Context.** Events must be tagged with the corridors they affect. PostGIS can do this, but tests
would then need a spatial database, and the corridors are small (hundreds of points).

**Decision.** Corridors are stored as simplified polylines (JSON). Matching is by road number first,
then by distance from the event geometry to the polyline using a flat-earth approximation, in pure
Python. Routes from Valhalla are simplified (Douglas-Peucker, 30 m) before storing.

**Consequences.** Fast, testable anywhere, accurate to well under the 500 m buffer at UK scale.
Re-tagging is a loop over live events, run whenever corridors change. If volumes grow, move the
distance test into PostGIS; the image already includes it.
