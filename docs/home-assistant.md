# Home Assistant

World Assistant publishes its data to Home Assistant over MQTT, using Home Assistant's
[MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery). Sensors appear on
their own, grouped into devices; there is no YAML to write and no custom integration to install.

The worker reads this service's own API every few minutes (so Home Assistant always shows exactly
what the web pages show) and publishes retained messages to the broker. Home Assistant needs only
its normal MQTT integration, pointed at the same broker.

## What appears

| Device | Sensors |
|---|---|
| **Pollen** | `Allergy risk` (0 to 10, today; the forecast is in its attributes), `Allergy risk label`, and one `Pollen: <name>` sensor per allergen (level today, with the next days in attributes). |
| **Fuel prices** | `Cheapest Petrol (E10)` and `Cheapest Diesel` near your default place, in pence. Attributes name the station and list the five nearest. |
| **Trains and buses** | For each departure board you have set up: `<board>: next departure` (minutes; the next services are in attributes) and `<board>: delayed or cancelled` (a count). Bus tracker sensors keep their own device (see [bus.md](bus.md)). |
| **Roads** | One `<corridor>: active works` sensor per enabled National Highways corridor, and `Live road events` (everything currently open). |
| **Events nearby** | `Events coming up` (count for the next seven days) and `Next event`, with the next five in attributes. |

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

## A starter dashboard

Add a manual card to any dashboard. Entity ids follow `sensor.wa_<device>_<sensor>`; check the exact
ids under Settings > Devices & services > MQTT.

```yaml
type: entities
title: Today
entities:
  - sensor.wa_pollen_allergy_risk_label
  - sensor.wa_fuel_cheapest_e10
  - sensor.wa_roads_live_events
  - sensor.wa_events_next_event
```

For the departure board, a Markdown card shows the attributes:

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
      message: "Pollen risk is {{ states('sensor.wa_pollen_allergy_risk_label') }} today."
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

State messages carry place names, station names, postcodes of fuel stations and event venues. They
do not carry your coordinates. Anyone who can read your broker can read them; if the broker is open
to your whole network, set a username and the `mqtt_password` secret.
