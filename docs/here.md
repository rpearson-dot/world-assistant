# Here

Everything World Assistant knows about where you are now, without saving it as a place first: for a stop somewhere
new. Open **Here** and press **Use my location**; each person's phone or computer uses its own location.

| Part | Where from | Covers |
| --- | --- | --- |
| The area (postcode, ward, district) | Code-Point Open | Great Britain |
| Bus stops within 500 m | NaPTAN | Great Britain |
| Buses about now (within a mile) | Bus Open Data Service | England |
| Stations within 2 km | TfL | London |
| Flood warnings | Environment Agency | England |
| Air quality index | Defra UK-AIR | UK (stations are sparse in the countryside) |
| Rivers, rain, tide | Environment Agency | England |
| Storm overflows | National Storm Overflow Hub | Thames Water's area |
| Pollen | Copernicus (CAMS) | Europe |
| Cheapest fuel (E10) within 5 miles | Fuel Finder | UK |
| GRIDSERVE chargers within 10 miles | GRIDSERVE | GRIDSERVE sites |
| Events in the next 3 days | Skiddle, Ticketmaster, local listings | where they list |
| Road disruption within 2 km | TfL | London |
| Power cuts within 3 miles | UK Power Networks | London, the South East and the East |

Each part is World Assistant's own answer for that point, so it works as it does for a saved place. A part outside its
area (London's stations and roads elsewhere) is left off; one that is switched off or failing says so, and the rest
still come back. **Save as a place** keeps it, so it gets Home Assistant sensors, trips and the map too.

## The location

The browser only shares its location with a secure page, so open World Assistant at its **https (Tailscale)
address**; at the plain `http://` address on the home network the page says why it cannot. The first time, allow
location for the page when the browser asks.

## Without the page

- **Assistant:** the `here` tool, with `lat` and `lon` (a Siri shortcut can send the phone's location) or a postcode,
  and `about` to ask for just some parts ("air,fuel").
- **API:** `GET /v1/here?lat=&lon=` (and `parts=` to choose), and `POST /v1/here/save {lat, lon, name}`.
