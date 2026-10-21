# Home Assistant

World Assistant publishes its data to Home Assistant over MQTT, using Home Assistant's
[MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery). Sensors appear on
their own, grouped into devices; there is no YAML to write and no custom integration to install.

The worker reads this service's own API every few minutes (so Home Assistant always shows exactly
what the web pages show) and publishes retained messages to the broker. Home Assistant needs only
its normal MQTT integration, pointed at the same broker.

## Where you are, for each person

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
| **Fuel prices**, **road closures and speed restrictions**, **pollen** | Published nationwide over MQTT with positions. The dashboards filter by distance from the viewer's position (or the `zone` at the top of each card). Nothing to set up per person. Pollen is a grid of about 50 points across Britain, refreshed three times a day. |
| **Events** | Looked up for one point at a time, so Home Assistant fetches them itself, once per person, passing that person's position. Needs the one-off setup below. |
| **Trains and buses** | Not location-based. Published over MQTT as they are. |

## What appears

Over MQTT (retained, with discovery):

| Device | Sensors |
|---|---|
| **Fuel prices** | `<fuel>: national median` (with the cheapest station in attributes), and one `<fuel> tile` sensor per half-degree map square (roughly 55 by 35 km). A tile's state is how many stations it holds; its attributes list every station with price, brand, postcode and coordinates, cheapest first. Petrol (E10) and diesel. |
| **Trains and buses** | For each departure board you have set up: `<board>: next departure` (minutes; the next services are in attributes) and `<board>: delayed or cancelled` (a count). Bus tracker sensors keep their own device (see [bus.md](bus.md)). |
| **Roads** | `Live road events`, one `<corridor>: active works` sensor per enabled National Highways corridor, and one `Road events tile` sensor per half-degree map square that has any. A tile's attributes list its closures and speed restrictions (unplanned ones, plus planned works starting within a week) with road, direction, description, times and positions. |

| **Pollen** | One `Pollen tile` sensor per 2 degree map square (state: the number of grid points it holds; attributes: each point's position, allergy risk and allergen levels for four days), and `image.wa_pollen_map`, a colour-coded map of Britain's allergy risk. The map is only re-sent when the forecast changes. |

From the static pollen package (`homeassistant/packages/world_assistant_pollen.yaml`), for your home zone:

| Entity | What it is |
|---|---|
| `sensor.wa_pollen_home_allergy_risk` | Allergy risk today (0 to 10) at the grid point nearest `zone.home` (within 150 km, else unknown), with `nearest_km` and `label` in attributes. |
| `sensor.wa_pollen_home_alder`, `_birch`, `_grasses`, `_mugwort`, `_olive`, `_ragweed` | Each allergen's level today (0 to 4), so each has its own history for the graphs. |

From the generated package (REST, hourly), for each person (`<p>` is the part after `person.`):

| Entity | What it is |
|---|---|
| `sensor.wa_location_<p>` | Where that person is: state `phone` or `home zone`, with `latitude` and `longitude` attributes. |
| `sensor.wa_events_<p>_upcoming` | How many events in the next 7 days within 8 km; the list is in its attributes. |
| `sensor.wa_events_<p>_next` | The next event's name, with date, venue and distance in attributes. |

If a data source is not set up (no pollen key, no events key), that part is simply empty.

### How the pollen grid works

The pollen provider answers one point at a time and asks for no more than about one call every four hours per point. So the worker fetches a fixed lattice of points over Britain (1 degree apart, about 51 points on land; `WA_POLLEN_GRID_DEG`) at 02:40, 10:40 and 18:40, pausing 10 seconds between calls, and stores them in the `pollen_grid` table. It stops if the provider fails four times in a row and tries again at the next run. Anything asking for pollen at a point (`/v1/pollen/near?lat=..&lon=..`, or Home Assistant's tiles) is answered from the nearest stored point within 150 km and under 24 hours old, falling back to a live fetch otherwise. `GET /v1/pollen/grid` returns the whole lattice. Each stored point records when its forecast last changed (`changed_at`), which shows whether the provider updates every four hours or daily.

## Setting up pollen and events (once)

Pollen needs no per-person setup: copy `homeassistant/packages/world_assistant_pollen.yaml` to `/config/packages/` (steps 3 and 6 below enable packages and restart). It contains no secrets and reads the pollen tiles nearest `zone.home`. The rest of this section is for events.

1. Find each person's entity id in Settings > People (click the person, then the cog; for example
   `person.alex`). Each needs the mobile app signed in as that person, with location tracking on,
   if you want their position while away.
2. On the NAS, from the project folder, generate the files (use your server's address and port,
   `WA_API_PORT`, default 8010):
   ```
   python3 scripts/ha-setup.py --host 192.168.1.10:8010 person.alex person.sam
   ```
   If the NAS has no `python3`, run it in a container instead:
   ```
   docker run --rm -v "$PWD":/w -w /w python:3-slim python scripts/ha-setup.py --host 192.168.1.10:8010 person.alex person.sam
   ```
   This writes `ha-out/world_assistant.yaml` (events and each person's location). Add `--radius-km` or `--days`
   to change how far and how many days of events (default 8 km, 7 days). It writes no secrets.
3. In Home Assistant's `configuration.yaml`, make sure packages are on:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
4. Copy `world_assistant.yaml` to `/config/packages/world_assistant.yaml`.
5. In `/config/secrets.yaml`, add one line with the contents of `api_token.txt` (the token never goes
   in the package):
   ```yaml
   wa_authorization: "Bearer <contents of api_token.txt>"
   ```
6. Developer tools > YAML > Check configuration, then Restart Home Assistant.
7. Add the dashboards (below). Re-run the script and replace the file whenever you add
   or remove a person.

The coordinates go to World Assistant only as parameters of each request. It doesn't store them or
use any saved place. Pollen is answered from the stored grid, so the pollen provider never sees where anyone is. While a person is away, the events services (Skiddle, Ticketmaster) receive wherever they are, not their home.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `WA_MQTT_HOST`, `WA_MQTT_PORT`, `WA_MQTT_USER`, secret `mqtt_password` | | The broker. |
| `WA_MQTT_ENABLED` | `true` | Turns all MQTT publishing off, including the bus tracker. |
| `WA_HA_PUBLISH` | `true` | Turns off just these sensors. |
| `WA_HA_INTERVAL_MIN` | `5` | How often the worker publishes. |
| `WA_HA_API_URL` | `http://api:8000` | Where the worker finds the API. |
| `WA_MQTT_TOPIC_PREFIX` | `world_assistant` | State topics are `<prefix>/<device>/<sensor>`. |
| `WA_MQTT_DISCOVERY_PREFIX` | `homeassistant` | Must match the MQTT integration in Home Assistant. |

Each sensor tells Home Assistant to mark it `unavailable` if nothing arrives for three intervals, so
a stopped worker shows as unavailable rather than as old numbers that look current.

## Ready-made dashboards

Ready-made dashboards use only Home Assistant's built-in cards (no HACS):

- In [`homeassistant/dashboards/`](../homeassistant/dashboards): `fuel.yaml`, `trains-buses.yaml`,
  `roads.yaml`, `events.yaml` and `pollen.yaml`. They find their entities by name, so they need no editing for
  boards or corridors. Fuel and roads have a `zone` and a radius at the top of each card, used when
  a viewer has no matching person. Events needs the generated package; the pollen history graph needs the static pollen package.
- `pollen.yaml` shows the pollen map (`image.wa_pollen_map`), the viewer's own pollen this week (from the nearest grid point), and a history graph of the home location's allergy risk and allergens.

Fuel, roads, events and pollen show each viewer their own location's data, as described above.

To add one:

1. Settings > Dashboards > Add dashboard > New dashboard from scratch. Name it (for example "Pollen").
2. Open it, then the pencil (top right) > the three dots > Raw configuration editor.
3. Delete what is there, paste the whole file, Save.

Repeat for each.

## Filtering by a Home Assistant location

Fuel stations near a zone, as a template sensor (Settings > Devices & services > Helpers > Template,
or in `configuration.yaml`). Change `zone.home` for any zone (or `sensor.wa_location_<person>` for where
that person is) and `5` for the radius in km:

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
  - sensor.wa_events_alex_next
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
3. Add the pollen package, and generate and add the package for events (above), then the dashboards. Build automations on the `sensor.wa_*` entities. Replace older template sensors,
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
