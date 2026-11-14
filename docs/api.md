# API reference

Every endpoint World Assistant answers, grouped by area. This page is written from the code
(`python scripts/api-doc.py`), and a test fails when it falls behind. Your server's live reference, with every
parameter and a "try it" button, is at `/docs`.

**Access.** From outside the home network, `/v1/*` needs `Authorization: Bearer <api_token>`. On the home network,
and for the web app in a browser on your tailnet, no token is needed ([security](security.md#access)). The web
pages themselves hold no data: each one calls these endpoints.


**Areas:** [airstage](#airstage) · [ask](#ask) · [audit](#audit) · [auto-deploy](#auto-deploy) · [backups](#backups) · [bank-holidays](#bank-holidays) · [bikes](#bikes) · [bus](#bus) · [bus-disruptions](#bus-disruptions) · [buslog](#buslog) · [clients](#clients) · [conditions](#conditions) · [crime](#crime) · [energy](#energy) · [environment](#environment) · [ev](#ev) · [events](#events) · [floods](#floods) · [food](#food) · [fuel](#fuel) · [graph](#graph) · [ha](#ha) · [health](#health) · [here](#here) · [live-buses](#live-buses) · [map](#map) · [modules](#modules) · [notify](#notify) · [ocpp](#ocpp) · [outdoors](#outdoors) · [outside](#outside) · [ovms](#ovms) · [places](#places) · [planner](#planner) · [pollen](#pollen) · [postcodes](#postcodes) · [power](#power) · [roads](#roads) · [settings](#settings) · [setup](#setup) · [shopping](#shopping) · [solar](#solar) · [stations](#stations) · [status](#status) · [stops](#stops) · [tariffs](#tariffs) · [tfl](#tfl) · [trips](#trips) · [vehicle-checks](#vehicle-checks) · [vehicles](#vehicles) · [weather](#weather) · [wishlist](#wishlist) · [Other](#other)


## airstage

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/airstage` | Every unit: on or off, mode, temperatures, fan, extras, error code, filter, and when it last answered. |
| PUT | `/v1/airstage/units` | The units: a name, the address on your home network, and the device id (the Airstage app's unit details, or the label on the unit's Wi-Fi adapter). Only private addresses: a unit is never asked about over the internet. |
| GET | `/v1/airstage/{uid}/history` | History |
| POST | `/v1/airstage/{uid}/set` | Change a unit: any of power, mode (auto, cool, dry, fan_only, heat), target_c (half degrees), fan (auto, quiet, low, medium_low, medium, medium_high, high), swing and the extras. Read back afterwards. |

## ask

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/ask` | Ask Index |
| POST | `/v1/ask/{name}` | POST the arguments as JSON. `?format=text` returns just the sentence, ready to speak. |

## audit

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/audit` | Changes made through the API, and refused tokens, newest first. |

## auto-deploy

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/auto-deploy` | Get State |
| PUT | `/v1/auto-deploy` | Set Switch |
| POST | `/v1/auto-deploy/report` | Report |
| DELETE | `/v1/auto-deploy/request` | Take back an "Update now" the server has not picked up yet. |
| POST | `/v1/auto-deploy/request` | Update now: the server's every-minute job picks it up. Asking again while one is open changes nothing. |
| POST | `/v1/auto-deploy/request/claim` | The server's job takes the open request (409 when there is none to take, or another job has it). |

## backups

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/backups` | List All |
| POST | `/v1/backups` | Create One |
| GET | `/v1/backups/{name}` | Download |

## bank-holidays

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/bank-holidays` | The next bank holidays (in the division set by WA_BANK_HOLIDAY_DIVISION), and whether today is one. |

## bikes

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/bikes` | Everything for a rider: bikes with their wheels and parts (each with its wear), loose sets, the due list, batteries, the shelf, kit, rides to confirm as wet or not, tyre pressures and running cost. |
| POST | `/v1/bikes/bikes` | A bike with the parts its answers give it: the type's defaults, with any answers given changed. Its wheels are two labelled sets ("<bike> front wheel", "<bike> rear wheel"), so they can be swapped and renamed. |
| DELETE | `/v1/bikes/bikes/{bike_id}` | Delete a bike added by mistake, with the parts and wheels on it. A bike with rides is retired instead (sold, stolen, written off), which keeps its rides, parts, history and cost. |
| PATCH | `/v1/bikes/bikes/{bike_id}` | Change a bike. Changed answers (a rear shock added, the front brake changed) add the parts the new answers give and retire the ones they no longer give; the retired ones' history stays in the log. |
| DELETE | `/v1/bikes/bikes/{bike_id}/frame-photo` | Delete Frame Photo |
| GET | `/v1/bikes/bikes/{bike_id}/frame-photo` | Frame Photo |
| PUT | `/v1/bikes/bikes/{bike_id}/frame-photo` | A photo of the frame number (under the bottom bracket, usually): for insurance, BikeRegister and the police if it is stolen. |
| POST | `/v1/bikes/bikes/{bike_id}/groupset` | A groupset as the starting point for a bike: its answers set to match (shifting, rings, speeds, bars, a hydraulic brake's fluid), which adds or retires parts as any change of answers does (a front derailleur for two rings), then the make, model and details of each of its parts filled in, wheels' too (the cassette, a rotor). |
| GET | `/v1/bikes/bikes/{bike_id}/photo` | Bike Photo |
| PUT | `/v1/bikes/bikes/{bike_id}/photo` | Put Bike Photo |
| GET | `/v1/bikes/catalogue` | Bike types, the choices for each answer, each kind of part with its default life, and each type's defaults. |
| POST | `/v1/bikes/checklist` | A bike's pre-ride or service check, ticked off: logged (which items were done), and the items that are the garage's own checks (the tyre pressures, the hanger, the spokes, the timing chain) restart those parts' counts. |
| GET | `/v1/bikes/costs` | Get Costs |
| POST | `/v1/bikes/fit` | Move a part or a set, or every wheel of a group: onto a bike, into a set (a part), or off. Swapping a wheelset onto a bike takes the wheels it replaces off that bike. A past `at` re-counts the rides since then (until the next move). |
| GET | `/v1/bikes/garmin` | Get Status |
| DELETE | `/v1/bikes/garmin/key` | Forget Key |
| PUT | `/v1/bikes/garmin/key` | Check the key with Garmin, then keep it in the keys folder. A key Garmin refuses is not kept. |
| PUT | `/v1/bikes/garmin/rules` | Put Rules |
| POST | `/v1/bikes/garmin/sync` | Sync Now |
| POST | `/v1/bikes/jobs` | A service list done in one go: replacements (with a spare, or a new part, its make and model if bought), services and checks, parts binned (retired: off the bike or shelf, kept in the history and costs), and things to buy onto the Outdoors list. Each job's result; one failing does not stop the rest. |
| POST | `/v1/bikes/lookup` | A product page's name, brand and picture, from the schema.org data or Open Graph tags it publishes, to fill in a part. The picture is linked, credited to the page, not copied. |
| POST | `/v1/bikes/parts` | A part: fitted (to a bike or a set), on the shelf (a spare, with how many), or rider kit (a helmet, shoes, an AXS battery). |
| DELETE | `/v1/bikes/parts/{part_id}` | Delete a part added by mistake, with its log. A part that was used and is done with is replaced or retired instead, which keeps its history and cost. |
| PATCH | `/v1/bikes/parts/{part_id}` | Change Part |
| GET | `/v1/bikes/parts/{part_id}/options` | What could replace a part: the spares on the shelf (those that fit first, each with why not and what would not go with the rest of the bike) and things to buy (the same again, the bike's groupset's, others that fit). |
| GET | `/v1/bikes/parts/{part_id}/photo` | Part Photo |
| PUT | `/v1/bikes/parts/{part_id}/photo` | Put Part Photo |
| POST | `/v1/bikes/parts/{part_id}/remove` | Take a part off for good: the bike does not have it (a dropper listed on a bike with a fixed seatpost, say). A part with no history is deleted; one with history is retired, which keeps it in the log and costs. A part one of the bike's answers gives changes that answer (the dropper to none), so the parts that go with it go too; any other part the answers give is remembered as removed, so changing the answers later does not add it back. |
| POST | `/v1/bikes/parts/{part_id}/to-list` | Put a part (its brand and model, or its kind) on the Outdoors list in KitchenOwl. |
| POST | `/v1/bikes/recalls/check` | Look for recalls now (the worker does it weekly). |
| POST | `/v1/bikes/recalls/dismiss` | A notice read and not about your part: not shown again. |
| GET | `/v1/bikes/riders` | Riders |
| POST | `/v1/bikes/riders` | Add Rider |
| GET | `/v1/bikes/rides` | List Rides |
| POST | `/v1/bikes/rides` | A ride by hand. Outdoors, it is given a wet suggestion from the rain at the time, to confirm. |
| POST | `/v1/bikes/rides/bulk` | Answer many rides at once: every unanswered outdoor ride dry; every bike as it is; or every ride without a bike the bike its kind of ride (from Garmin, or its CSV) goes to. |
| POST | `/v1/bikes/rides/import` | Garmin Connect's activities CSV. Rides already imported are skipped; outdoor rides of the last four weeks get a wet suggestion from the rain gauges. |
| DELETE | `/v1/bikes/rides/{ride_id}` | Delete a ride (logged twice, or not a ride at all): its wear comes off the parts. An imported ride deleted here comes back if the same file is imported again. |
| PATCH | `/v1/bikes/rides/{ride_id}` | Say whether a ride was wet (the suggestion is only that), or which bike it was on (Garmin often keeps the last bike chosen): either confirms it. |
| GET | `/v1/bikes/routes` | The routes you ride again and again: rides from the last `routes_years` (5) ridden more than `routes_more_than` (5) times, each with how often, how far, when first and last, and whether it is watched for road events. |
| DELETE | `/v1/bikes/routes/{route_id}/watch` | Unwatch Route |
| POST | `/v1/bikes/routes/{route_id}/watch` | Watch a route like a trip: its corridor kept (150 m either side) so road works and closures along it are found. |
| POST | `/v1/bikes/service` | Log what was done. A replacement retires the old part and fits the new one (a spare from the shelf, taking one off it, or a new part) in the same place, with its count from now. A service, check, charge or top-up restarts the part's count. A crash puts its checks on the due list. A cost or a note is just kept. |
| DELETE | `/v1/bikes/service/{service_id}` | Undo a logged service, check, charge or cost: the part's count goes back to what it was. Undoing a replacement also puts the old part back where it was and takes the new one away (a spare goes back on the shelf). A replacement whose new part has had things logged since is refused: undo those first. |
| POST | `/v1/bikes/sets` | A labelled set: a wheel (front or rear) or any group of parts. A new wheel can come with the usual parts for a bike (from its answers: tyre, tube or sealant, tyre check, rotor, cassette, hub...), and be fitted straight away, the wheel it replaces going into storage. |
| PATCH | `/v1/bikes/sets/{set_id}` | Change Set |
| GET | `/v1/bikes/settings` | Get Settings  |
| PUT | `/v1/bikes/settings` | Put Settings |

## bus

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/bus/lines/{line}/stops` | The stops on a line in each direction, for choosing a journey. London lines come from TfL (with arrival predictions); any other line in Britain comes from BODS (GPS positions and the operator's timetable). |
| POST | `/v1/bus/settings` | Change the times of a tracker without touching its route or history. Meant for Home Assistant, so it answers {"ok", "message"} rather than an error code. History already recorded keeps the deadline it was recorded under; the new one applies from the next scheduled bus. |
| POST | `/v1/bus/setup` | Create or replace a tracker from plain choices; works out everything else. With a way-back window it also creates `<name>-return`: the other side of each stop, from where you got off to where you got on. |
| GET | `/v1/bus/trackers` | Get Trackers |
| DELETE | `/v1/bus/trackers/{tid}` | Remove Tracker |
| GET | `/v1/bus/trackers/{tid}` | Get Tracker |
| PUT | `/v1/bus/trackers/{tid}` | Create or replace a tracker. Replacing restarts tracking and re-reads the route. |
| GET | `/v1/bus/trackers/{tid}/anomalies` | Recent anomalies. The bookkeeping `first_seen` entries, and the "reported at an earlier stop" entries recorded before 0.23.2 (the predictions shifting, not the bus), are left out unless asked for with `?type=`. |
| GET | `/v1/bus/trackers/{tid}/conditions` | Leg times today, last 30 minutes against the day's average. |
| GET | `/v1/bus/trackers/{tid}/live` | Buses being followed right now, and the next arrivals at each stop of interest. |
| GET | `/v1/bus/trackers/{tid}/log` | Success and failure of every scheduled bus on every recorded day: did a bus from the boarding stop reach the target (either side of the road) before the deadline? |
| GET | `/v1/bus/trackers/{tid}/positions` | GPS fixes of the buses the tracker followed inside its journey window (from BODS), oldest first. Default: today. Each bus's fixes, in order, are its track along the route. |
| POST | `/v1/bus/trackers/{tid}/refresh-route` | Forget the stored stop order and timetable so the next poll re-reads them from TfL. |
| GET | `/v1/bus/trackers/{tid}/reliability` | How often each scheduled slot actually produced a bus at the target stop. |
| GET | `/v1/bus/trackers/{tid}/summary` | Summary |
| GET | `/v1/bus/trackers/{tid}/target` | The run to the target stop: which buses made it before the deadline, and which is the last. `outlook` is the chance each remaining bus gets there by the deadline (weekday mornings only). |
| GET | `/v1/bus/trackers/{tid}/terminations` | A daily log of buses that ended a trip short of the end of the route while the tracker was watching: where they were last seen and when, whether they turned back (and where they next appeared) or just stopped appearing. Newest day first. |

## bus-disruptions

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/bus-disruptions` | Current disruptions near your places (or near a point), closest first. Empty where no authority publishes. |

## buslog

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/buslog/calendar` | The school calendar from today on (what is over is taken out each night). |
| PUT | `/v1/buslog/calendar` | The school's terms, holidays and INSET days: {school, terms: [{name, from, to}], breaks: [...], inset: [dates], notes}. |
| GET | `/v1/buslog/polls` | Every minute of a day, per source: requests that answered with data, answered empty, or failed. A gap in the record shows here as a failed (or missing) request, never as a bus that did not come. |
| GET | `/v1/buslog/positions` | Each bus's last BODS position at a moment (default now) within `within_s` before it, and how old it was then. |
| GET | `/v1/buslog/predictions` | TfL's prediction changes on a day: new, moved (by how much) and gone, for one stop or bus if given. |
| GET | `/v1/buslog/raw/{poll_id}` | A request's response exactly as it came (kept a school year). |
| DELETE | `/v1/buslog/record-now` | Stop Now |
| POST | `/v1/buslog/record-now` | Record from the next minute for a while, whatever the day and time (the worker starts within a minute). |
| GET | `/v1/buslog/route` | The route as recorded on a day (or the newest before it): each direction's stops in order, and its line. |
| POST | `/v1/buslog/schedule-now` | Take today's route and timetable from TfL now (as the recorder does before the window), kept like any other request. Takes a few seconds. |
| GET | `/v1/buslog/settings` | Get Settings  |
| PUT | `/v1/buslog/settings` | The route, the window, and the test switch that records every day (weekends and holidays too). |
| GET | `/v1/buslog/status` | Whether it is recording now and why, the next recording days, and how today's requests went per source. |
| GET | `/v1/buslog/stop/{stop}` | A stop's next hour: the buses the timetable has there, and the ones TfL expects (as it stood at `at`). |
| GET | `/v1/buslog/vehicle/{vehicle}` | A bus's remaining stops to the end of its route, as TfL expected at `at`, against the timetable: on time, early or late at each, and by how much. |

## clients

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/clients` | Every client: name, access, paths, the token's last four characters, made, last used, revoked. |
| POST | `/v1/clients` | A new client and its token. The token is shown this once: copy it into the client now. |
| POST | `/v1/clients/{cid}/revoke` | Stop a client's token working, now. The client stays listed, with when it was revoked. |

## conditions

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/conditions` | Conditions on every enabled trip that has a route; a trip that cannot be read is listed with the reason. |

## crime

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/crime` | Street-level crime within a mile and the local police team, for a point or a postcode; and the borough's crime rate (a year per 1,000 residents) against the boroughs next to it. |

## energy

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/energy/now` | The current rate and band, the next change, the next cheap and peak windows, and the cheapest block. |
| GET | `/v1/energy/rates` | The upcoming rates, each with its band. |
| GET | `/v1/energy/schedule` | The inverter's six daily slots for this tariff: start times, which charge from the grid, and what each charging slot must cover until the next cheap window (for the Home Assistant energy plan). |
| GET | `/v1/energy/sessions` | Saving Sessions (with whether you joined) and Octopus boost events: on now, next, upcoming. |
| GET | `/v1/energy/usage` | Each day from the meter (Octopus, half-hourly): kWh bought and sold, what was bought cost at your rates, and how much was bought 16:00 to 19:00. Octopus publishes a day's readings the next day. |
| GET | `/v1/energy/windows` | Cheapest block of `hours` in the next 24 hours, and the banded windows. |

## environment

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/environment/air` | Air quality: the nearest recent reading of NO2, ozone, PM2.5 and PM10, and Defra's Daily Air Quality Index (1-3 low, 4-6 moderate, 7-9 high, 10 very high): the highest of the four. |
| GET | `/v1/environment/flows` | River flow near a place, where a gauge estimates it: cubic metres a second now, the trend, and how it compares with that river's usual flows there (very low below Q95 to very high above Q05, and as a share of the mean). |
| GET | `/v1/environment/overflows` | Storm overflows (Thames Water's, from the National Storm Overflow Hub) within `km`, and those on the watched rivers within `river_km`: which are discharging now, which have in the last 48 hours, and which are offline. |
| GET | `/v1/environment/overflows/rivers` | The rivers whose storm overflows are watched, upstream as well as near home (within 15 km). |
| PUT | `/v1/environment/overflows/rivers` | Set the rivers to watch, e.g. {"rivers": ["Hogsmill", "Thames"]}. An empty list watches only what is near. |
| GET | `/v1/environment/rain` | Rain gauges near a place: millimetres in the last hour and the last 24 hours. |
| GET | `/v1/environment/rivers` | River level gauges near a place: the level now, its usual range there (low, normal, high) and the trend. |
| GET | `/v1/environment/summary` | Home's air, rivers and rain; the air index for every place; a tide gauge for places within 10 km of one. Each part that fails says so in its own `error` and the rest still come back. |
| GET | `/v1/environment/tides` | The nearest tide gauge: the measured level and whether it is rising or falling. Not a prediction. |

## ev

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/ev/all` | Every site, briefly: position, chargers working and free (in all and by connector), open now, the fastest, the price and how often one has been out of order lately. For Home Assistant, which picks what is near whoever is looking; and the connector your electric vehicles take, if they agree. |
| GET | `/v1/ev/sites` | Charging sites nearest a saved place (home by default) or a point, or whose name matches `q`. |
| GET | `/v1/ev/sites/{site_id}` | One site, with each charger and its status, and how reliable it has been. |
| GET | `/v1/ev/trips/{trip_id}` | Charging sites along a trip's route, in the order you pass them, with how far along each is. |

## events

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/events/mutes` | The muted event names and words: left out of the Events page, Here, the assistant, Home Assistant and the graph. |
| PUT | `/v1/events/mutes` | Set the muted names and words, e.g. {"names": ["Quiz night"], "words": ["tribute", "bingo"]}. |
| GET | `/v1/events/near` | Events from every configured source (Skiddle, Ticketmaster), merged and sorted by date; muted ones left out. |

## floods

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/floods` | Every current warning at least as serious as `max_level`, most serious first. |
| GET | `/v1/floods/at` | Live warnings covering a point (or within `within_km` of it), most serious first. |

## food

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/food/alerts` | Get Alerts |
| POST | `/v1/food/alerts/check` | Check Now |
| GET | `/v1/food/hygiene` | Food hygiene ratings near a point, nearest first (search by name too). |

## fuel

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/fuel/all` | Every station with a current price, nationwide and unranked (for Home Assistant to filter). |
| GET | `/v1/fuel/along/{corridor_id}` | Along |
| GET | `/v1/fuel/near` | Near |
| GET | `/v1/fuel/summary` | What is stored, and how fresh it is. |

## graph

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/graph/about` | What the graph is made of, and how much of it there is. |
| GET | `/v1/graph/counts` | How many of each node and relationship the graph currently holds. |
| GET | `/v1/graph/disruption` | What is currently disrupting the trips that start or end at a place. |
| GET | `/v1/graph/expand` | One node and everything joined to it. |
| GET | `/v1/graph/neighbours` | Everything joined to one thing, in either direction. |
| GET | `/v1/graph/overview` | Your places, trips, vehicles and bus trackers, and what they connect to: up to `per` each, so every place shows, not only the busiest. |
| GET | `/v1/graph/presets` | Ready-made questions for the Graph page. |
| POST | `/v1/graph/query` | A preset, or your own read-only Cypher. Anything that would write or reach outside is refused. |
| POST | `/v1/graph/rebuild` | Rebuild the graph from Postgres now, rather than waiting for the scheduled run. |
| GET | `/v1/graph/search` | Nodes whose name or id contains the text. |

## ha

| Method | Path | What it does |
|---|---|---|
| DELETE | `/v1/ha/api` | Forget Api |
| GET | `/v1/ha/api` | Get Api |
| PUT | `/v1/ha/api` | Home Assistant's address and a long-lived token (Profile > Security), for live checks in Find my setup. The token is kept in the keys folder, readable only by World Assistant, and never shown again. |
| POST | `/v1/ha/discover` | Read Home Assistant's setup over the saved Samba share, check it, and keep the result (GET /v1/ha/inventory). |
| GET | `/v1/ha/files/{kind}/{name}` | File |
| GET | `/v1/ha/inventory` | What the last "Find my setup" found (empty until it has run). |
| PUT | `/v1/ha/inverter` | The inverter's entity name, used in every copy and download of the files that name it (the energy plan). |
| POST | `/v1/ha/leftovers/remove` | Delete from Home Assistant the World Assistant sensors that Find my setup found are no longer provided: an empty retained discovery message removes each. Checked again against what is published now, so nothing current goes. |
| GET | `/v1/ha/locations` | List Locations |
| POST | `/v1/ha/locations` | Add a place, or renew one with the same name (which also updates its position). |
| POST | `/v1/ha/locations/delete` | Delete By Name |
| GET | `/v1/ha/locations/events` | Events near each place. Each search is cached for the refresh interval, so calling this often does not cost provider requests. |
| DELETE | `/v1/ha/locations/{loc_id}` | Delete Location |
| PUT | `/v1/ha/names` | Names to write the files with (any subset; an empty value puts the default back). Used in every copy and download of the files that name them. |
| GET | `/v1/ha/routes` | List Routes |
| POST | `/v1/ha/routes` | Add Route |
| POST | `/v1/ha/routes/delete` | Delete Route |
| GET | `/v1/ha/setup` | The files for this version, the secrets their packages use, and how publishing to the broker is going. |
| DELETE | `/v1/ha/smb` | Forget Smb |
| GET | `/v1/ha/smb` | The share in use (never the password) and the last copy. |
| PUT | `/v1/ha/smb` | Put Smb |
| POST | `/v1/ha/smb/copy` | Copy this version's files into Home Assistant's config folder. |
| POST | `/v1/ha/smb/test` | Connect to the share with these details (or the saved password) and look at what is there. |
| GET | `/v1/ha/token` | The API token, for Home Assistant's secrets.yaml. Only for someone who could already use the API. |
| POST | `/v1/ha/zones/copy` | Write that package into Home Assistant's packages folder over the saved Samba share. |
| POST | `/v1/ha/zones/import` | Make World Assistant places of these Home Assistant zones (from the last "Find my setup"). |
| GET | `/v1/ha/zones/package` | World Assistant's places that are not Home Assistant zones, as a package of zones (from the last "Find my setup"; all places if it has not run). |

## health

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/health/watch` | The latest self-check: problems now (worker, jobs, disks, backups, restore check), database size, and the last restore check. Runs a check if none is recent. |

## here

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/here` | Everything World Assistant knows about a point in Great Britain (or a postcode's centre), each part with a sentence, its status (ok, off, no_answer, refused, error) and its data. |
| POST | `/v1/here/save` | Keep the point as a saved place, so the rest of World Assistant (sensors, trips, the map) uses it too. |
| GET | `/v1/here/stream` | As /v1/here, but each part as soon as it answers (newline-delimited JSON: a "head", each "part", then "done"), each waiting up to 45 seconds. The Here page uses this. |

## live-buses

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/live-buses` | Buses reporting a position near a place or point, nearest first. |
| GET | `/v1/live-buses/departures` | Next buses at the bus stops nearest a point, outside London as well as in it: each bus on its way, minutes worked out from where it is along its route (at a typical speed, so not seeing traffic), and failing that the timetable's next weekday times. A line whose timetable is still being fetched says so; ask again shortly. |

## map

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/map/geojson` | Geojson |
| GET | `/v1/map/layers` | What can be drawn, and how much of it there is. |

## modules

| Method | Path | What it does |
|---|---|---|
| DELETE | `/v1/modules` | Back to the default: every module on. |
| GET | `/v1/modules` | List Modules |
| PUT | `/v1/modules` | Save the choice: everything except `off`, or only `on` (plus what those need, plus the core). |
| POST | `/v1/modules/{module_id}/disable` | Disable |
| POST | `/v1/modules/{module_id}/enable` | Enable |
| POST | `/v1/modules/{module_id}/test` | Try the module's key with one small live call. Never returns the key. |

## notify

| Method | Path | What it does |
|---|---|---|
| POST | `/v1/notify/check` | Check every step to the phone that can be checked from the server, in order, and send a test message. |
| GET | `/v1/notify/log` | Get Log |
| GET | `/v1/notify/settings` | Get Notify |
| PUT | `/v1/notify/settings` | Put Notify |
| POST | `/v1/notify/test` | A test notification now (quiet hours and the switches do not apply). |

## ocpp

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/ocpp` | Every charger World Assistant has heard from, with its state, the last week's restarts, drops and faults, and the address to give a charger. |
| PUT | `/v1/ocpp/mode` | The master switch: off (the charger refuses to charge), on (charges whenever the car is plugged in) or auto (Home Assistant's charging plan decides). Kept, and applied to every connected charger now. |
| PUT | `/v1/ocpp/settings` | Which chargers may connect (by id; none listed: any on the home network), how often to ask for meter readings while charging, and whether to publish the charger to Home Assistant. |
| POST | `/v1/ocpp/{cid}/command` | Any of the commands in protocol.COMMANDS: Reset {type: Soft\|Hard}, GetConfiguration {keys}, ChangeConfiguration {key, value}, TriggerMessage {message}, UnlockConnector, ChangeAvailability {type}, ClearCache. |
| GET | `/v1/ocpp/{cid}/events` | Events |
| GET | `/v1/ocpp/{cid}/messages` | The newest messages either way, exactly as they went. |
| GET | `/v1/ocpp/{cid}/sessions` | Sessions |
| POST | `/v1/ocpp/{cid}/start` | Start charging now (OCPP RemoteStartTransaction on connector 1). |
| POST | `/v1/ocpp/{cid}/stop` | Stop the charging session the charger last said it started. |

## outdoors

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/outdoors` | Every item on the KitchenOwl Outdoors list with the products it follows: price now and before, the lowest seen, the shop's "was" price, stock, and whether it has dropped in the last seven days. |
| POST | `/v1/outdoors/check` | Price every item now (or one). Takes a second or two per item. |
| POST | `/v1/outdoors/items` | Put an item on the KitchenOwl Outdoors list and price it now. |
| DELETE | `/v1/outdoors/items/{key}` | Tick an item off the KitchenOwl Outdoors list (its prices stay, for if it comes back). |
| GET | `/v1/outdoors/settings` | Which KitchenOwl list is the Outdoors list, and the household's lists to choose from. |
| PUT | `/v1/outdoors/settings` | Choose the KitchenOwl list to use (none: the one called Outdoors). Not the shopping list. |
| GET | `/v1/outdoors/{key}/history` | Every price each product this item follows has had, newest first. |

## outside

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/outside` | List Outside |
| GET | `/v1/outside/keys/all` | Every key the outside modules declare and whether each is set (for the Keys page). |
| POST | `/v1/outside/preview` | Read a repository's newest release and its manifest, and start the image checks. Nothing runs yet. |
| GET | `/v1/outside/removed/{mid}` | Removed |
| GET | `/v1/outside/runner/next` | The oldest waiting job for the runner (it marks it taken). |
| POST | `/v1/outside/runner/{jid}/done` | Runner Done |
| GET | `/v1/outside/{mid}` | Get One |
| POST | `/v1/outside/{mid}/approve` | Install a held update, agreeing to anything more it asks for (what you tick). |
| PUT | `/v1/outside/{mid}/auto-update` | Auto Update |
| POST | `/v1/outside/{mid}/install` | Install a previewed module with what you agreed to share. Refused while its checks have a problem (high or critical vulnerabilities can be accepted for this version with allow_vulnerable). |
| PUT | `/v1/outside/{mid}/keys/{name}` | One of the keys its manifest declares; given to it when it next starts (it is restarted now). |
| POST | `/v1/outside/{mid}/remove` | Remove every trace of it: its container, storage and images (the runner), then its keys, token, Home Assistant entities, settings, history and its row. GET /v1/outside/removed/{mid} has the report. |
| PUT | `/v1/outside/{mid}/settings` | Its settings, checked against the fields its manifest declares (World Assistant draws them; the module never adds its own). |

## ovms

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/ovms` | Every car heard from: battery, range, 12 V, charging, locked, where, how recently it reported, its warnings now and its last notifications. `vehicle` links an OVMS vehicle id to a vehicle on the Vehicles page. |
| PUT | `/v1/ovms/settings` | Whether to listen, the topic prefix the car publishes under (OVMS: Config → Server V3 → topic prefix; "+" for any part), and which Vehicles-page vehicle each OVMS vehicle id is. |
| GET | `/v1/ovms/{vehicle}/history` | Metric History |
| GET | `/v1/ovms/{vehicle}/metrics` | Every metric the car has sent, as it stands, with when. |
| GET | `/v1/ovms/{vehicle}/notices` | Notices |

## places

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/places` | List Places |
| DELETE | `/v1/places/{place_id}` | Delete Place |
| PUT | `/v1/places/{place_id}` | Put Place |

## planner

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/planner/charging` | The latest decision and why; how often it matched Home Assistant's plan; and each time they differed. |
| GET | `/v1/planner/demand` | How predictable evening demand is from the cold (a straight-line fit of each evening's kWh on its heating degree-hours), and tomorrow evening's likely need. Worked out at most every six hours unless fresh. |
| GET | `/v1/planner/reviews` | Each month (the last 12): what charging the car really cost and what it would have with the planner in charge, worked out on the 1st. Also the month so far. |
| GET | `/v1/planner/settings` | Get Settings  |
| PUT | `/v1/planner/settings` | The demand statistic and evening; the house battery and grid power entities for solar to spare (and which sign is export); and "act": World Assistant starts and stops the charger itself (Auto only). |

## pollen

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/pollen/grid` | The stored forecast for every point of the nationwide lattice (what Home Assistant filters). |
| GET | `/v1/pollen/history` | Recorded pollen levels per day (up to 90 days): each allergen, their total, and the allergy risk. |
| GET | `/v1/pollen/near` | Pollen levels by allergen for today and the next three days, plus the allergy risk. |

## postcodes

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/postcodes/near` | The nearest postcodes to a point: a reverse lookup, for a pin dropped on a map. |
| GET | `/v1/postcodes/{postcode}` | One postcode's position. |

## power

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/power/cuts` | Power cuts now near a saved place (home by default) or a point, the ones at the place first. |
| GET | `/v1/power/history` | Every power cut recorded at a saved place (home by default) in the last `days`: how long it lasted, how many homes, and whether power came back later than first promised. Kept for good (ukpn_fault_history). |
| GET | `/v1/power/supply` | What supplies a place: whether it is on UK Power Networks' network, the grid supply point, primary and secondary substation feeding it, and power cuts there now. |
| GET | `/v1/power/works` | UK Power Networks' street works near a place: under way (open) and planned (proposed). |

## roads

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/roads/tfl` | Every current or scheduled disruption, unplanned first, worst first. |
| GET | `/v1/roads/tfl/at` | Disruptions within `radius_m` of a point, nearest first. |

## settings

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/settings/keys` | Which keys are set and where each comes from (settings, file, vault, env). Never a value. |
| POST | `/v1/settings/keys/to-vault` | Copy every key saved here or in a secret file into the vault, then (by default) forget the copies saved here, so the vault is where they come from. Each is checked by reading it back through the app's own vault login before its copy here is removed. The token is used for this one write and not kept. |
| DELETE | `/v1/settings/keys/{name}` | Forget the key saved here; the secret file (if any) applies again. |
| PUT | `/v1/settings/keys/{name}` | Save Key |
| GET | `/v1/settings/look` | Get Look |
| PUT | `/v1/settings/look` | Put Look |
| DELETE | `/v1/settings/mqtt` | Forget the settings saved here (and the password saved here): .env applies again. |
| GET | `/v1/settings/mqtt` | The broker in use, where the settings came from, and how the last publish went. Never the password. |
| PUT | `/v1/settings/mqtt` | Save the broker settings (they win over .env). An empty server turns publishing off. |
| GET | `/v1/settings/mqtt/ca` | The CA certificate (public, not a secret), for the OVMS module and Home Assistant to trust. |
| POST | `/v1/settings/mqtt/test` | Try to connect with these settings (and this password, or the saved one), without saving anything. |

## setup

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/setup` | The setup checklist: what each source unlocks, whether it is set up and working, and where to get a key. |

## shopping

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/shopping/basket/{retailer}` | What is in the retailer's basket now (needs that retailer's login). |
| POST | `/v1/shopping/basket/{retailer}` | Put the last priced list's products for this retailer in its basket, each up to the quantity on the list. Items already there are topped up, never doubled. It never books a slot or checks out: you finish on their site. |
| POST | `/v1/shopping/clear` | Empty the whole list ({confirm: true}). The items stay in KitchenOwl's recent items, quick to add back. |
| GET | `/v1/shopping/list` | Get List |
| POST | `/v1/shopping/list` | Add an item to the KitchenOwl list (as the assistant's add_to_list does). |
| GET | `/v1/shopping/lists` | Each household with its shopping lists (id, name, how many items), to choose from. |
| POST | `/v1/shopping/login/{retailer}/code` | The code the retailer texted (Sainsbury's) or emailed, while its login waits. |
| POST | `/v1/shopping/login/{retailer}/continue` | After clicking the link the retailer emailed: look again, and finish the login if it is confirmed. |
| GET | `/v1/shopping/login/{retailer}/screenshot` | The newest screenshot of the retailer's sign-in page: what it showed when a login failed or waited. |
| POST | `/v1/shopping/login/{retailer}/start` | Log in with the email and password saved on the Shopping page. Sainsbury's then waits for its SMS code. |
| POST | `/v1/shopping/logout/{retailer}` | Logout |
| DELETE | `/v1/shopping/match` | Forget Match |
| PUT | `/v1/shopping/match` | Set Match |
| GET | `/v1/shopping/orders/{retailer}` | Recent orders at a retailer (Sainsbury's: upcoming ones too; Ocado: delivered ones only), each with the list items it covers, ready to tick off. Needs that retailer's login. |
| GET | `/v1/shopping/price` | Last Price |
| POST | `/v1/shopping/price` | Price the list at each chosen retailer: best match or your usual per item, totals, and the cheaper one. |
| POST | `/v1/shopping/session/{retailer}` | Cookies exported from your own browser after logging in there: the fallback when the login here is refused. |
| PUT | `/v1/shopping/settings` | Save Settings |
| GET | `/v1/shopping/slots` | The next delivery slots at one retailer, read only, kept for an hour (Sainsbury's needs a browser: 10-20 s). |
| GET | `/v1/shopping/status` | What is set up and reachable. Always answers; each part says what is wrong with it. |
| POST | `/v1/shopping/tick` | Take these items off the list (they have been ordered, say). They stay in KitchenOwl's recent items. |

## solar

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/solar` | Today's and tomorrow's expected kWh, what is still to come today, the power expected now, and each hour. |
| GET | `/v1/solar/accuracy` | Each recent day: the total forecast the evening before, and what the panels made (Home Assistant's statistic chosen on the page). How far off it usually is says how far to trust tomorrow's. |
| POST | `/v1/solar/from-home-assistant` | The arrays set in Home Assistant's Forecast.Solar integration, read from its files over the share set on the Home Assistant page (not saved: check them, then Save). |
| POST | `/v1/solar/refresh` | Fetch the forecast now (uses one of the hour's 12 requests for each array). |
| GET | `/v1/solar/settings` | Get Settings  |
| PUT | `/v1/solar/settings` | Your arrays (up to 6: forecast.solar allows 12 requests an hour, one an array) and the Home Assistant statistic that is the panels' total output (for the accuracy check). |

## stations

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/stations` | Stations whose name contains q, with how many closures each has now. |
| GET | `/v1/stations/alerts` | What is closed now: at the stations given (and others in the same building), or everywhere. |
| GET | `/v1/stations/near` | The stations closest to a saved place (home by default) or a point. |
| GET | `/v1/stations/{station_id}` | A station in full: facilities, entrances, platforms, and closures now. |

## status

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/status` | Status |
| GET | `/v1/status/events` | Live stored events, optionally only those tagged to a corridor. |
| GET | `/v1/status/events/map` | Every live road event nationwide with where it is, for a client that filters by its own location (Home Assistant). Unplanned events always; planned only if they start soon. |
| GET | `/v1/status/jobs` | Jobs |

## stops

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/stops/near` | Stops within a radius, nearest first. |
| GET | `/v1/stops/{atco}` | One stop by ATCO code, the id TfL and the bus tracker use. |

## tariffs

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/tariffs` | The last comparison (worked out only when asked: POST /v1/tariffs/compare) and the settings. |
| POST | `/v1/tariffs/compare` | Work the comparison out again now (it fetches every Octopus tariff: about half a minute). |
| PUT | `/v1/tariffs/settings` | Count readings from (when the house last changed); your house battery's usable kWh and charge rate (for the shifted costs); your region if your tariff code does not say; and tariffs from other suppliers' quotes. |

## tfl

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/tfl/boards` | List Boards |
| DELETE | `/v1/tfl/boards/{board_id}` | Delete Board |
| PUT | `/v1/tfl/boards/{board_id}` | Put Board |
| GET | `/v1/tfl/boards/{board_id}/departures` | Departures |
| GET | `/v1/tfl/lines/status` | Lines Status |
| GET | `/v1/tfl/stations` | Find a station or stop id by name (tfl), or a three-letter station code (nationalrail). |

## trips

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/trips` | List Trips |
| DELETE | `/v1/trips/{trip_id}` | Delete Trip |
| PUT | `/v1/trips/{trip_id}` | Put Trip |
| GET | `/v1/trips/{trip_id}/conditions` | Conditions now, and planned works, on a trip's route. |
| GET | `/v1/trips/{trip_id}/cost` | Fuel needed and cost for a routed trip, at typical and cheapest prices on the route. |
| POST | `/v1/trips/{trip_id}/route` | Route the trip with Valhalla and (re)build its corridor. |

## vehicle-checks

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/vehicle-checks` | List Checks |
| POST | `/v1/vehicle-checks/{vehicle_id}/check` | Check Now |
| PUT | `/v1/vehicle-checks/{vehicle_id}/registration` | Set Registration |

## vehicles

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/vehicles` | List Vehicles |
| GET | `/v1/vehicles/home-rate` | The rate an electric vehicle's trip cost uses when no home rate is given: the Energy tab's cheapest. |
| DELETE | `/v1/vehicles/{vehicle_id}` | Delete Vehicle |
| PUT | `/v1/vehicles/{vehicle_id}` | Put Vehicle |

## weather

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/weather` | Now, the next 48 hours, and today plus the next six days in brief (low, high, rain, sunshine, heating degree-hours all day and 16:00 to 19:00). |
| GET | `/v1/weather/history` | Each past day as it turned out: low, high, rain, sunshine and heating degree-hours (all day and 16:00 to 19:00). |

## wishlist

| Method | Path | What it does |
|---|---|---|
| GET | `/v1/wishlist` | Every wish on the KitchenOwl wishlist: `now` is what is coming or on sale, `last_seen` the latest match, `last_on_sale` the latest on-sale date any match had. |
| POST | `/v1/wishlist/check` | Search Aldi and Lidl now, for every wish or one. Takes a second or two per wish. |
| POST | `/v1/wishlist/items` | Put a wish on the KitchenOwl wishlist, as adding it in the app does, and check Aldi and Lidl for it. |
| DELETE | `/v1/wishlist/items/{key}` | Tick a wish off the KitchenOwl wishlist (its history stays, for if it comes back). |
| GET | `/v1/wishlist/settings` | Which KitchenOwl list is the wishlist, and the household's lists to choose from. |
| PUT | `/v1/wishlist/settings` | Choose the KitchenOwl list to use as the wishlist (none: the one called Wishlist). Not the shopping list. |
| GET | `/v1/wishlist/{key}/seen` | Every product that matched this wish, the latest first. |

## Other

| Method | Path | What it does |
|---|---|---|
| GET | `/healthz` | Liveness: the process is up. Does not touch dependencies. |
| GET | `/healthz/errors` | How many server errors this process has answered since it started, and its version (for updates). |
| GET | `/mcp` | Mcp Get |
| POST | `/mcp` | Mcp Endpoint |
| GET | `/readyz` | Readiness: Postgres and Redis are reachable. |
