# Live buses

The Bus Open Data Service (BODS) publishes where every bus that reports a position is right now, for
England, Scotland and Wales. World Assistant asks for the buses in a box around a place and shows them.

This is a **snapshot**, not a record. The [bus tracker](bus.md) follows one TfL route all morning
and keeps the history; this shows every route, anywhere, as it is this moment, and keeps nothing.

## Turning it on

1. Register (free) at data.bus-data.dft.gov.uk and copy your API key.
2. Put it in the `bods_api_key` secret: `/mnt/user/appdata/world-assistant/secrets/bods_api_key.txt`, or in
   OpenBao like the others. `bash scripts/init-secrets.sh` creates the empty file.
3. Restart the stack (`up -d`).

Without a key the endpoint answers 503 and the map layer says why. Nothing else changes.

## Using it

| Where | What |
| --- | --- |
| `GET /v1/live-buses?place=home&radius_km=2&line=213` | buses within 2 km of a place, nearest first (`lat=`/`lon=` for a point) |
| the `buses` map layer | the same, drawn; off at first, and around a place only |

The radius is capped at 15 km: a box that size around a city is thousands of buses. Results are cached for
30 seconds per area, so refreshing the map is not a stream of requests to BODS.

## What is in it

Each bus carries its route (the published line number), operator code, destination, direction, bearing, the
vehicle id, and the time its position was reported. Buses with no usable position are dropped. Which buses
appear depends on the operator: BODS only has the ones that send positions.

## Not yet verified

This was written without being able to reach BODS. The request (`/api/v1/datafeed/` with `boundingBox` as
`minLon,minLat,maxLon,maxLat` and `api_key`) and the SIRI-VM fields come from BODS client documentation, and
the tests use a response built to that shape. The parser ignores XML namespaces and fields it does not know.
If your first call fails, the error says whether it was the key, the HTTP status, or the XML.

Contains public sector information licensed under the Open Government Licence v3.0.

## Does it cover London (the 213)?

Unconfirmed. BODS says its positions refresh every 10 seconds, but its guidance page does not say whether
London buses are included, and TfL has its own feed. To find out with your key, ask for a box round the
213's route and look for line 213:

```
curl -s "https://data.bus-data.dft.gov.uk/api/v1/datafeed/?boundingBox=-0.40,51.40,-0.20,51.50&api_key=YOUR_KEY" | grep -c "<VehicleActivity>"
```

Zero (or no 213) means London is not in it, and the TfL-based bus tracker stays the source for that route.
