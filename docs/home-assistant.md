# Home Assistant

World Assistant publishes its data to Home Assistant over MQTT, using Home Assistant's
[MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery). Sensors appear on
their own, grouped into devices; there is no YAML to write and no custom integration to install.

The worker reads this service's own API every few minutes (so Home Assistant always shows exactly
what the web pages show) and publishes retained messages to the broker. Home Assistant needs only
its normal MQTT integration, pointed at the same broker.

## Where the places live

World Assistant's "default place" belongs to its own web pages. Nothing sent to Home Assistant uses
it. Home Assistant has its own locations (zones), so it does the filtering:

- **Fuel** is published for the whole country. You filter by distance from any zone in a template.
- **Pollen and events** can only be fetched for a point, so they are published for each place saved
  in World Assistant (`/places`). Save a place for each Home Assistant location you want them for;
  each appears as its own device.
- **Road closures and speed restrictions** are published nationwide with their positions, like fuel. The roads dashboard shows the ones near a Home Assistant zone (`zone.home` unless you change it).
- **Trains and buses** are not location-based and are published as they are.

## What appears

| Device | Sensors |
|---|---|
| **Fuel prices** | `<fuel>: national median` (with the cheapest station in attributes), and one `<fuel> tile` sensor per half-degree map square (roughly 55 by 35 km). A tile's state is how many stations it holds; its attributes list every station with price, brand, postcode and coordinates, cheapest first. Petrol (E10) and diesel. |
| **Pollen: `<place>`** | `Allergy risk` (0 to 10, today; the forecast is in its attributes), `Allergy risk label`, and one `Pollen: <name>` sensor per allergen. |
| **Events: `<place>`** | `Events coming up` (count for the next seven days) and `Next event`, with the next five in attributes. |
| **Trains and buses** | For each departure board you have set up: `<board>: next departure` (minutes; the next services are in attributes) and `<board>: delayed or cancelled` (a count). Bus tracker sensors keep their own device (see [bus.md](bus.md)). |
| **Roads** | `Live road events`, one `<corridor>: active works` sensor per enabled National Highways corridor, and one `Road events tile` sensor per half-degree map square that has any. A tile's attributes list its closures and speed restrictions (unplanned ones, plus planned works starting within a week) with road, direction, description, times and positions. |

If a data source is not set up (no pollen key, no events key), that device is simply left out.

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
for boards, corridors or places. The fuel dashboard has a `zone`, a radius and a fuel at the top of
each card.

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

Other entities, e.g. a card per location:

```yaml
type: entities
title: Today
entities:
  - sensor.wa_pollen_home_allergy_risk_label
  - sensor.wa_fuel_national_e10
  - sensor.wa_roads_live_events
  - sensor.wa_events_home_next_event
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
    entity_id: sensor.wa_pollen_home_allergy_risk
    above: 6
action:
  - service: notify.notify
    data:
      message: "Pollen risk is {{ states('sensor.wa_pollen_home_allergy_risk_label') }} today."
```

## Rebuilding Home Assistant around this

1. Install the MQTT integration in Home Assistant (broker address, same as `WA_MQTT_HOST`).
2. Let the worker run for one cycle (`WA_HA_INTERVAL_MIN` minutes). The devices appear.
3. Build dashboards and automations on the `sensor.wa_*` entities. Replace older template sensors,
   REST sensors and add-ons that fetched the same data by other routes.
4. To remove an entity you no longer want, publish an empty retained message to its discovery topic:
   `mosquitto_pub -h <broker> -t homeassistant/sensor/wa_<device>_<sensor>/config -n -r`.
   To clear leftovers from older experiments, list what the broker holds with
   `mosquitto_sub -h <broker> -t 'homeassistant/#' -v -W 5 --retained-only`.

## Privacy

Messages carry place names you saved, station names and postcodes, and event venues. Fuel stations
come with their (public) coordinates. Your own coordinates are never published, and World Assistant
never learns your Home Assistant zones: the filtering happens inside Home Assistant. Anyone who can
read your broker can read all of this; if the broker is open to your whole network, set a username
and the `mqtt_password` secret.
