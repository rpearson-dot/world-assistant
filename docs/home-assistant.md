# Home Assistant

World Assistant publishes its data to Home Assistant over MQTT, using Home Assistant's
[MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery). Sensors appear on
their own, grouped into devices; there is no YAML to write and no custom integration to install.

The worker reads this service's own API every few minutes (so Home Assistant always shows exactly
what the web pages show) and publishes retained messages to the broker. Home Assistant needs only
its normal MQTT integration, pointed at the same broker.

## Where you are, for each person (fuel, roads, pollen)

World Assistant's "default place" belongs to its own web pages. Nothing sent to Home Assistant uses
it. Anything that depends on where you are is worked out **per person**, so each viewer sees data
for where *they* are, not where someone else in the house is:

- Each person's position is the Home Assistant mobile app's location (through their `person`
  entity) while they are **away from home** and it reports one accurate to within 1 km. At home, or
  with no usable position, it is `zone.home`. Being at home deliberately uses the zone, not the
  phone's GPS, so answers don't jitter.
- The dashboards use the logged-in viewer's name to pick their person, so they show that person's
  location. **A person's name must match their Home Assistant user's name** (it does by default when
  the person was created from the user). A viewer with no matching person gets the zone.

How each kind of data gets there:

| Data | How it gets to Home Assistant |
|---|---|
| **Fuel prices**, **road closures and speed restrictions**, **pollen** | Published nationwide over MQTT with positions. The dashboards filter by distance from the viewer's position (or the `zone` at the top of each card). Nothing to set up per person. Pollen is a grid of about 50 points across Britain, refreshed a few points at a time, twice a day. |
| **Events** | Looked up for one point at a time, so Home Assistant fetches them itself for `zone.home` (hourly) and the dashboard shows what is on near home. Needs the one-off setup below. Events do not follow the viewer. |
| **Trains and buses** | Not location-based. Published over MQTT as they are. |

## What appears

Over MQTT (retained, with discovery):

| Device | Sensors |
|---|---|
| **Fuel prices** | `<fuel>: national median` (with the cheapest station in attributes), and one `<fuel> tile` sensor per half-degree map square (roughly 55 by 35 km). A tile's state is how many stations it holds; its attributes list every station with price, brand, postcode and coordinates, cheapest first. Petrol (E10) and diesel. Prices up to 30 days old are published (`WA_HA_FUEL_MAX_AGE_H`), each with its age in hours (`age_h`), because most stations only report a price when it changes; a tile too big for Home Assistant's 16 KB attribute limit is split into `…_p2`, `…_p3`. The national median uses only prices under 48 hours old. The Fuel dashboard shows prices up to a week old within 16 km by default and marks ones older than two days with ⚠️; change `radius_km` and `max_age_h` at the top of the card. A dropdown at the top picks Petrol or Diesel (add `homeassistant/packages/world_assistant_fuel.yaml` to your packages folder; the choice is shared by everyone who opens the dashboard). |
| **Trains and buses** | For each departure board you have set up: `<board>: next departure` (minutes; the next services are in attributes) and `<board>: delayed or cancelled` (a count). Bus tracker sensors keep their own device (see [bus.md](bus.md)). |
| **Roads** | `Live road events`, one `<corridor>: active works` sensor per enabled National Highways corridor, and one `Road events tile` sensor per half-degree map square that has any. A tile's attributes list its closures and speed restrictions (unplanned ones, plus planned works starting within a week) with road, direction, description, times and positions. |

| **Pollen** | One `Pollen <lat>, <lon>` sensor per forecast point (`sensor.wa_pollen_point_<lat>_<lon>`, state: allergy risk today; attributes: position, each allergen's level per day, worst first), shown as markers on the dashboard's map card (tap one for its breakdown). Also one `Pollen tile` sensor per 2 degree map square (state: the number of grid points it holds; attributes: each point's position, allergy risk and allergen levels for four days), and `image.wa_pollen_map`, a colour-coded map of Britain's allergy risk. The map is only re-sent when the forecast changes. |

From the static pollen package (`homeassistant/packages/world_assistant_pollen.yaml`), for your home zone:

| Entity | What it is |
|---|---|
| `sensor.wa_pollen_home_allergy_risk` | Allergy risk today (0 to 10) at the grid point nearest `zone.home` (within 150 km, else unknown), with `nearest_km` and `label` in attributes. |
| `sensor.wa_pollen_home_alder`, `_birch`, `_grasses`, `_mugwort`, `_olive`, `_ragweed` | Each allergen's level today (0 to 4), so each has its own history for the graphs. |

From the events package (REST, hourly), for the home zone:

| Entity | What it is |
|---|---|
| `sensor.wa_events_home_upcoming` | How many events in the next 7 days within 8 km of `zone.home`; the list is in its attributes. |
| `sensor.wa_events_home_next` | The next event's name, with date, venue and distance in attributes. |

If a data source is not set up (no pollen key, no events key), that part is simply empty.

### Extra places for events

Your home zone's events come straight from Home Assistant. For anywhere else (a parent's house, a
holiday), ask World Assistant to watch it. From Developer tools > Actions (or an automation):

```yaml
action: rest_command.wa_add_location
data:
  name: Mum's house
  lat: 52.48
  lon: -1.9
  # radius_km: 12   (optional)
```

Within one publish cycle a `sensor.wa_events_place_mum_s_house` appears (state: number of events; the
list is in its attributes), and the events dashboard's "Other places" card lists every
`sensor.wa_events_place_*` it finds, so nothing in the dashboard needs editing. A zone works too:
`lat: "{{ state_attr('zone.work', 'latitude') }}"`. `rest_command.wa_remove_location` (with `name`)
takes one away.

Each place costs one search on each events source every twelve hours, so the number is capped and places
time out:

- At most `WA_HA_LOCATIONS_MAX` (8) places. Adding a ninth is refused with a message listing the others;
  adding a name that already exists renews it.
- A place disappears (sensor and all) if it is not renewed for 30 days. Once six of the eight are in use
  the time-out drops to 7 days, so forgotten places clear sooner.
- To keep one, renew it. A weekly automation that calls `rest_command.wa_add_location` for the places you
  care about does it.
- Searches are cached for the refresh interval, so opening the dashboard costs nothing. The
  `sensor.wa_events_locations` sensor shows how many are used and the estimated provider requests a day.

Only the place's name and position are stored (table `ha_location`).

### Adding places and routes from Home Assistant (the Manage dashboard)

`homeassistant/dashboards/manage.yaml` and `homeassistant/packages/world_assistant_manage.yaml` put the
two things above, and routes, behind buttons, so nothing needs a terminal. The package adds four text
helpers (`WA place`, `WA place name`, `WA route from`, `WA route to`) and four scripts. Type a **zone**
(`zone.work`) or a **position** (`52.48, -1.9`) into a helper, then press the button:

- **Watch events at a place** / **Stop watching** (`script.wa_add_location`, `script.wa_remove_location`):
  as above, with the name taken from the zone, or from `WA place name` for a bare position.
- **Add a route** / **Remove route** (`script.wa_add_route`, `script.wa_remove_route`): From and To are each a
  zone or a position. World Assistant saves them as two places and a trip in each direction, builds the
  route with the routing engine (so it needs the routing stack, see the runbook; without it the route is saved but not built, and
  pressing Add route again once the engine is running builds it), and the route's corridor shows up as a `Roads` sensor, so closures along
  your regular drive appear on the Roads dashboard. Routing is local, so a route costs no outside
  requests, but the number is still capped: `WA_HA_ROUTES_MAX` (default 12) trips, each direction counting
  as one. Adding the same two ends again updates the route.

Every script ends with a notification saying what happened (the route's distance and time, a refusal
because a cap was reached, or why a position could not be read); the API answers `{"ok", "message"}`
rather than an error code for these reasons. Places and trips made this way are marked
(`place.origin`/`trip.origin` = `home-assistant`, ids start `ha-`), so they never touch anything made on
the web pages, the daily pollen history ignores them, and removing a route removes only its own
places, trips and corridor. The dashboard's "Currently set up" card lists what exists. The routes need
two more secrets (below).

### How the pollen grid works

The pollen provider answers one point at a time and, importantly, allows only **40 requests a day** on a key. So everything shares one daily budget of 36 requests (`WA_POLLEN_DAILY_LIMIT`; the day is the UTC date), and the worker keeps a lattice of points over Britain (1 degree apart, about 51 points on land; `WA_POLLEN_GRID_DEG`) in the `pollen_grid` table. Because each fetch covers four days (today and three more), a stored point stays useful for up to four days: lookups read the entry for today's date. At 03:40 and 15:40 the worker refreshes the 12 stalest points (`WA_POLLEN_GRID_CALLS_PER_RUN`), pausing 10 seconds between calls, so every point is refreshed roughly every two days. It stops early when only 6 requests of the day's budget are left (`WA_POLLEN_RESERVE_CALLS`, kept for saved places and live lookups), and gives up if the provider fails four times in a row. Anything asking for pollen at a point (`/v1/pollen/near?lat=..&lon=..`, or Home Assistant's tiles) is answered from the nearest stored point within 150 km that still covers today, falling back to a live fetch otherwise (which uses budget). `GET /v1/pollen/grid` returns the lattice, each cut to start today. Each stored point records when its forecast last changed (`changed_at`), which shows how often the provider really updates.

## Setting up pollen and events (once)

Neither needs any per-person setup, and neither package writes a secret into its file.

1. In Home Assistant's `configuration.yaml`, make sure packages are on:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
2. Copy `homeassistant/packages/world_assistant_pollen.yaml` and
   `homeassistant/packages/world_assistant_events.yaml` to `/config/packages/`. Optional, for the matching
   dashboards: `world_assistant_fuel.yaml` (the Petrol/Diesel dropdown), `world_assistant_manage.yaml`
   (add places and routes; needs the events package too) and `world_assistant_bus.yaml` (change the bus
   tracker's times). The events, bus and routes packages each carry a small automation or script, so if your
   `configuration.yaml` has `automation:` or `script:` of its own, make sure they are lists/maps that
   packages can merge into (the usual `!include automations.yaml` works).
3. In `/config/secrets.yaml`, add these lines (the address is your server's and `WA_API_PORT`, default
   8010; the token is the contents of `api_token.txt`):
   ```yaml
   wa_events_url: "http://192.168.1.10:8010/v1/events/near"
   wa_locations_url: "http://192.168.1.10:8010/v1/ha/locations"
   wa_locations_delete_url: "http://192.168.1.10:8010/v1/ha/locations/delete"
   wa_authorization: "Bearer <contents of api_token.txt>"
   ```
   and, for the optional packages, `wa_routes_url` (`…/v1/ha/routes`), `wa_routes_delete_url`
   (`…/v1/ha/routes/delete`) and `wa_bus_settings_url` (`…/v1/bus/settings`) in the same way. Home Assistant
   will not start a config with a missing secret, so add every secret a package you copied refers to.
4. Developer tools > YAML > Check configuration, then Restart Home Assistant.
5. Add the dashboards (below).

To change how far or how many days of events (default 8 km, 7 days), edit `radius_km` and `days` in the events package.

The home zone's coordinates go to World Assistant only as parameters of each events request. It doesn't store them or
use any saved place. Pollen is answered from the stored grid, so the pollen provider never sees where anyone is. The events services (Skiddle, Ticketmaster) receive your home zone's position.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `WA_MQTT_HOST`, `WA_MQTT_PORT`, `WA_MQTT_USER`, secret `mqtt_password` | | The broker. |
| `WA_MQTT_ENABLED` | `true` | Turns all MQTT publishing off, including the bus tracker. |
| `WA_HA_PUBLISH` | `true` | Turns off just these sensors. |
| `WA_HA_INTERVAL_MIN` | `5` | How often the worker publishes. |
| `WA_POLLEN_DAILY_LIMIT`, `WA_POLLEN_GRID_CALLS_PER_RUN`, `WA_POLLEN_RESERVE_CALLS` | `36`, `12`, `6` | The pollen request budget (above). |
| `WA_HA_LOCATIONS_MAX` | `8` | Most extra places for events (below). |
| `WA_HA_ROUTES_MAX` | `12` | Most trips Home Assistant may add (each direction is one). |
| `WA_HA_FUEL_MAX_AGE_H` | `720` | Fuel prices up to this old (hours) are published, each with its age. |
| `WA_HA_LOCATION_TTL_DAYS` | `30` | A place not renewed for this long is dropped. |
| `WA_HA_LOCATION_PRESSURE_TTL_DAYS` | `7` | The shorter time-out once 75% of the cap is used. |
| `WA_HA_LOCATION_REFRESH_H` | `12` | How often each place's events are searched again. |
| `WA_HA_LOCATION_RADIUS_KM`, `WA_HA_LOCATION_DAYS` | `8`, `7` | How far and how many days, for extra places. |
| `WA_HA_API_URL` | `http://api:8000` | Where the worker finds the API. |
| `WA_MQTT_TOPIC_PREFIX` | `world_assistant` | State topics are `<prefix>/<device>/<sensor>`. |
| `WA_MQTT_DISCOVERY_PREFIX` | `homeassistant` | Must match the MQTT integration in Home Assistant. |

Each sensor tells Home Assistant to mark it `unavailable` if nothing arrives for three intervals, so
a stopped worker shows as unavailable rather than as old numbers that look current.

## Ready-made dashboards

Ready-made dashboards use only Home Assistant's built-in cards (no HACS):

- In [`homeassistant/dashboards/`](../homeassistant/dashboards): `fuel.yaml`, `trains-buses.yaml`,
  `roads.yaml`, `events.yaml`, `pollen.yaml` and `manage.yaml`. They find their entities by name, so they need no editing for
  boards or corridors. Fuel and roads have a `zone` and a radius at the top of each card, used when
  a viewer has no matching person. Fuel has a Petrol/Diesel dropdown (the fuel package). Manage needs the manage package. Events needs the events package; the pollen history graph needs the static pollen package.
- `pollen.yaml` shows the pollen map (`image.wa_pollen_map`), a tappable map of the forecast points (the card lists the 51 points of the default 1 degree lattice; change `WA_POLLEN_GRID_DEG` and its list needs regenerating), the viewer's own pollen this week (from the nearest grid point), and a history graph of the home location's allergy risk and allergens.

Fuel, roads and pollen show each viewer their own location's data, as described above.

To add one:

1. Settings > Dashboards > Add dashboard > New dashboard from scratch. Name it (for example "Pollen").
2. Open it, then the pencil (top right) > the three dots > Raw configuration editor.
3. Delete what is there, paste the whole file, Save.

Repeat for each.

## Filtering by a Home Assistant location

Fuel stations near a zone, as a template sensor (Settings > Devices & services > Helpers > Template,
or in `configuration.yaml`). Change `zone.home` for any zone and `5` for the radius in km:

```yaml
template:
  - sensor:
      - name: Cheapest petrol near home
        unit_of_measurement: p
        state: >
          {% set z = states.zone.home %}
          {% set ns = namespace(r=[]) %}
          {% for t in states.sensor if t.entity_id.startswith('sensor.wa_fuel_tile_e10_') %}
            {% for s in t.attributes.stations | default([]) %}
              {% if distance(z.attributes.latitude, z.attributes.longitude, s.lat, s.lon) < 5 %}
                {% set ns.r = ns.r + [s.pence] %}
              {% endif %}
            {% endfor %}
          {% endfor %}
          {{ ns.r | min if ns.r else none }}
        attributes:
          nearby: >
            {% set z = states.zone.home %}
            {% set ns = namespace(r=[]) %}
            {% for t in states.sensor if t.entity_id.startswith('sensor.wa_fuel_tile_e10_') %}
              {% for s in t.attributes.stations | default([]) %}
                {% if distance(z.attributes.latitude, z.attributes.longitude, s.lat, s.lon) < 5 %}
                  {% set ns.r = ns.r + [s] %}
                {% endif %}
              {% endfor %}
            {% endfor %}
            {{ ns.r | sort(attribute='pence') }}
```

Show it on a dashboard with an entities card for the price and a Markdown card for the list
(`{% for s in state_attr('sensor.cheapest_petrol_near_home', 'nearby')[:5] %}`). The stations carry
`lat` and `lon`, so a map card can plot them too. Large attributes are not kept in history (Home
Assistant skips attributes over 16 KB when recording), which suits live prices.

Other entities, for a small "today" card:

```yaml
type: entities
title: Today
entities:
  - sensor.wa_pollen_alex_allergy_risk
  - sensor.wa_fuel_national_e10
  - sensor.wa_roads_live_events
  - sensor.wa_events_home_next
```

Check the exact ids under Settings > Devices & services > MQTT. For a departure board, a Markdown card:

```yaml
type: markdown
content: >
  {% set s = 'sensor.wa_trains_home_am_next' %}
  Next train to {{ state_attr(s, 'destination') }} in {{ states(s) }} min
  (platform {{ state_attr(s, 'platform') }}).
  {% for d in state_attr(s, 'departures') %}
  - {{ d.destination }} in {{ d.minutes }} min, {{ d.status }}
  {% endfor %}
```

## Example automation

Tell me when the pollen risk is high and I have not taken an antihistamine:

```yaml
alias: Pollen warning
trigger:
  - platform: numeric_state
    entity_id: sensor.wa_pollen_alex_allergy_risk
    above: 6
action:
  - service: notify.notify
    data:
      message: "Pollen risk is {{ state_attr('sensor.wa_pollen_alex_allergy_risk', 'allergy_risk_label')[0] }} today."
```

## Rebuilding Home Assistant around this

1. Install the MQTT integration in Home Assistant (broker address, same as `WA_MQTT_HOST`).
2. Let the worker run for one cycle (`WA_HA_INTERVAL_MIN` minutes). The devices appear.
3. Add the pollen and events packages (above), then the dashboards. Build automations on the `sensor.wa_*` entities. Replace older template sensors,
   REST sensors and add-ons that fetched the same data by other routes.
4. To remove an entity you no longer want, publish an empty retained message to its discovery topic:
   `mosquitto_pub -h <broker> -t homeassistant/sensor/wa_<device>_<sensor>/config -n -r`.
   To clear leftovers from older experiments, list what the broker holds with
   `mosquitto_sub -h <broker> -t 'homeassistant/#' -v -W 5 --retained-only`.

## Privacy

MQTT messages carry station names and postcodes, road names, and bus and train services. Fuel
stations and road events come with their (public) positions. Your own coordinates are never
published, and World Assistant never learns your Home Assistant zones. It receives the point (your home zone, or
your phone's position when you are away) only as the `lat`/`lon` of the events requests
Home Assistant sends, and does not store it. Anyone who can read
your broker can read the published data; if the broker is open to your whole network, set a username
and the `mqtt_password` secret. The API token sits only in Home Assistant's `secrets.yaml`.
