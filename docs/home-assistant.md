# Home Assistant

World Assistant publishes its data to Home Assistant over MQTT, using Home Assistant's
[MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery). Sensors appear on
their own, grouped into devices; there is no YAML to write and no custom integration to install.

The worker reads this service's own API every few minutes (so Home Assistant always shows exactly
what the web pages show) and publishes retained messages to the broker. Home Assistant needs only
its normal MQTT integration, pointed at the same broker.

## Where you are: the phone, else the home zone

World Assistant's "default place" belongs to its own web pages. Nothing sent to Home Assistant uses
it. Anything that depends on where you are uses one "where am I" sensor in Home Assistant,
`sensor.wa_location` (defined in the package below):

- **Away from home** and the Home Assistant mobile app is reporting a position (through your
  `person` entity, accurate to within 1 km): that position, so pollen, fuel, roads and events follow
  you.
- **At home**, or no usable position: `zone.home`. Being at home deliberately uses the zone, not the
  phone's GPS, so the answers don't jitter.

It reaches Home Assistant by one of two routes:

| Data | How it gets to Home Assistant |
|---|---|
| **Fuel prices**, **road closures and speed restrictions** | Published nationwide over MQTT with positions; the dashboards filter by distance from `sensor.wa_location` (or the zone set at the top of each card if the package isn't installed). |
| **Pollen**, **events** | Fetched by Home Assistant itself from World Assistant, passing `sensor.wa_location`'s coordinates (these can only be looked up for one point at a time). Needs the one-off setup below. |
| **Trains and buses** | Not location-based. Published over MQTT as they are. |

## What appears

Over MQTT (retained, with discovery):

| Device | Sensors |
|---|---|
| **Fuel prices** | `<fuel>: national median` (with the cheapest station in attributes), and one `<fuel> tile` sensor per half-degree map square (roughly 55 by 35 km). A tile's state is how many stations it holds; its attributes list every station with price, brand, postcode and coordinates, cheapest first. Petrol (E10) and diesel. |
| **Trains and buses** | For each departure board you have set up: `<board>: next departure` (minutes; the next services are in attributes) and `<board>: delayed or cancelled` (a count). Bus tracker sensors keep their own device (see [bus.md](bus.md)). |
| **Roads** | `Live road events`, one `<corridor>: active works` sensor per enabled National Highways corridor, and one `Road events tile` sensor per half-degree map square that has any. A tile's attributes list its closures and speed restrictions (unplanned ones, plus planned works starting within a week) with road, direction, description, times and positions. |

From the World Assistant package (REST, hourly), once set up:

| Entity | What it is |
|---|---|
| `sensor.wa_location` | Where Home Assistant thinks you are: state `phone` or `home zone`, with `latitude` and `longitude` attributes. |
| `sensor.wa_pollen_allergy_risk` | Allergy risk today, 0 to 10. The forecast and every allergen's levels are in its attributes. |
| `sensor.wa_pollen_alder`, `_birch`, `_grasses`, `_mugwort`, `_olive`, `_ragweed` | Each allergen's level today (0 to 4), so each has its own history. |
| `sensor.wa_events_upcoming` | How many events in the next 7 days within 8 km; the list is in its attributes. |
| `sensor.wa_events_next` | The next event's name, with date, venue and distance in attributes. |

If a data source is not set up (no pollen key, no events key), that part is simply empty.

## Setting up pollen and events (once)

These use a file in this repository: [`homeassistant/packages/world_assistant.yaml`](../homeassistant/packages/world_assistant.yaml).

1. In `configuration.yaml`, make sure packages are on:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
2. Copy the file to `/config/packages/world_assistant.yaml`.
3. In `/config/secrets.yaml`, add one line with the contents of `api_token.txt` (the token never goes in the package):
   ```yaml
   wa_authorization: "Bearer <contents of api_token.txt>"
   ```
4. In the package, replace `WA-HOST:8010` (it appears twice) with your server's address and port
   (`WA_API_PORT`, default 8010). Change `radius_km=8&days=7` for events if you like.
5. Search and replace `person.CHANGE_ME` with your person entity (Settings > People shows it, for
   example `person.alex`), so the app's location is used while you're away. Leave it as it is to
   always use `zone.home`.
6. Developer tools > YAML > Check configuration, then Restart Home Assistant.

The coordinates go to World Assistant only as parameters of each request. It doesn't store them or
use any saved place. Pollen is looked up for places about 1 km apart and cached for four hours, so
travelling can mean up to one fetch an hour from the pollen provider, in line with its fair-use
guidance. Remember that while you're away the pollen provider and the events services (Skiddle,
Ticketmaster) receive wherever you are, rather than your home.

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

One dashboard per data source lives in [`homeassistant/dashboards/`](../homeassistant/dashboards):
`pollen.yaml`, `fuel.yaml`, `trains-buses.yaml`, `roads.yaml` and `events.yaml`. They use only
Home Assistant's built-in cards (no HACS), and find their entities by name, so they need no editing
for boards or corridors. The fuel and roads dashboards have a `zone` (used when the package isn't installed) and a radius
at the top of each card. The pollen and events dashboards, and the phone location, need the package above.

To add one:

1. Settings > Dashboards > Add dashboard > New dashboard from scratch. Name it (for example "Pollen").
2. Open it, then the pencil (top right) > the three dots > Raw configuration editor.
3. Delete what is there, paste the whole file, Save.

Repeat for each.

## Filtering by a Home Assistant location

Fuel stations near a zone, as a template sensor (Settings > Devices & services > Helpers > Template,
or in `configuration.yaml`). Change `zone.home` for any zone (or `sensor.wa_location` for where you
are) and `5` for the radius in km:

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
  - sensor.wa_pollen_allergy_risk
  - sensor.wa_fuel_national_e10
  - sensor.wa_roads_live_events
  - sensor.wa_events_next
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
    entity_id: sensor.wa_pollen_allergy_risk
    above: 6
action:
  - service: notify.notify
    data:
      message: "Pollen risk is {{ state_attr('sensor.wa_pollen_allergy_risk', 'allergy_risk_label')[0] }} today."
```

## Rebuilding Home Assistant around this

1. Install the MQTT integration in Home Assistant (broker address, same as `WA_MQTT_HOST`).
2. Let the worker run for one cycle (`WA_HA_INTERVAL_MIN` minutes). The devices appear.
3. Add the package for pollen and events (above), then the dashboards. Build automations on the `sensor.wa_*` entities. Replace older template sensors,
   REST sensors and add-ons that fetched the same data by other routes.
4. To remove an entity you no longer want, publish an empty retained message to its discovery topic:
   `mosquitto_pub -h <broker> -t homeassistant/sensor/wa_<device>_<sensor>/config -n -r`.
   To clear leftovers from older experiments, list what the broker holds with
   `mosquitto_sub -h <broker> -t 'homeassistant/#' -v -W 5 --retained-only`.

## Privacy

MQTT messages carry station names and postcodes, road names, and bus and train services. Fuel
stations and road events come with their (public) positions. Your own coordinates are never
published, and World Assistant never learns your Home Assistant zones. It receives the point (your home zone, or
your phone's position when you are away) only as the `lat`/`lon` of the pollen and events requests
Home Assistant sends, and does not store it. Anyone who can read
your broker can read the published data; if the broker is open to your whole network, set a username
and the `mqtt_password` secret. The API token sits only in Home Assistant's `secrets.yaml`.
