# Here

Everything World Assistant knows about where you are now, without saving it as a place first: for a stop somewhere
new. Open **Here** and press **Use my location**; each person's phone or computer uses its own location.

| Part | Where from | Covers |
| --- | --- | --- |
| The area (postcode, ward, district) | Code-Point Open | Great Britain |
| Next buses at the three nearest stops | TfL countdowns in London; elsewhere BODS (see below) | England |
| Bus disruptions within 2 km | BODS (the authorities that publish them) | where published |
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
| Crime within a mile over the last 12 published months (each month as bars), what happened to it, and the neighbourhood police team | data.police.uk (asked when you look, not stored) | England, Wales and Northern Ireland; about two months behind |
| Food hygiene ratings of the nearest food businesses | Food Standards Agency | England, Wales and Northern Ireland (Scotland's scheme is pass or improvement required) |

Each part is World Assistant's own answer for that point, so it works as it does for a saved place. A part outside its
area (London's stations and roads elsewhere) is left off; one that is switched off or failing says so, and the rest
still come back. **Save as a place** keeps it, so it gets Home Assistant sensors, trips and the map too.

## Next buses outside London

There are no countdowns outside London, so World Assistant works them out as its bus tracker does: the lines are the
ones BODS sees running within a few kilometres, each line's timetable comes from its operator's file on BODS, and each
bus's minutes to the stop come from where it is along that route at a typical bus speed (so traffic is not seen).
Where no bus is on its way yet, the timetable's next time is shown instead, on weekdays (the timetables read are
weekday ones). The first time a line is seen, its timetable can take some seconds to fetch: Here says which lines are
still being looked up, and they are there on the next look. Timetables are kept for twelve hours.

Bus disruptions come only from the authorities that publish them to BODS, and only while that feed is on
(`WA_BUS_DISRUPTIONS_POLL_ENABLED`); every current one is now kept with where it is, not only those near a saved place.

## The location

The browser only shares its location with a secure page, so open World Assistant at its **https (Tailscale)
address**; at the plain `http://` address on the home network the page says why it cannot. The first time, allow
location for the page when the browser asks.

## Without the page

- **Assistant:** the `here` tool, with `lat` and `lon` (a Siri shortcut can send the phone's location) or a postcode,
  and `about` to ask for just some parts ("air,fuel").
- **API:** `GET /v1/here?lat=&lon=` (and `parts=` to choose), and `POST /v1/here/save {lat, lon, name}`.
