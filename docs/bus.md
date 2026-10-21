# Bus tracker

Follows every bus on one TfL line, times the legs between stops you care about, and records
which buses reached a target stop before a deadline (weekdays only by default).

Nothing about a particular journey is in the code. You supply the stops as configuration.

## Set up (the easy way)

Open `/bus`, under **Set up a tracker** enter the route number and press **Find the stops**. Choose
where you get on and where you need to get to, add any busy stops to time on the way, set your
times and press **Start tracking**. The direction, the opposite-side stop at your destination (for
buses that terminate there) and the stop before it are worked out for you from TfL's route data.

## Configure by hand (advanced)

`PUT /v1/bus/trackers/{id}` with a body like:

```json
{
  "config": {
    "line": "213",
    "stops": [
      {"key": "start",     "sid": "<stop id>", "label": "Station"},
      {"key": "mid",       "sid": "<stop id>", "label": "Middle stop", "place": "mid"},
      {"key": "mid_back",  "sid": "<stop id>", "label": "Middle stop, other side", "place": "mid"},
      {"key": "before",    "sid": "<stop id>", "label": "Stop before the target"},
      {"key": "target",    "sid": "<stop id>", "label": "Target stop"},
      {"key": "target_back","sid": "<stop id>", "label": "Target, return side"}
    ],
    "target": {"board": "start", "alight": "target", "alight_return": "target_back",
               "before_alight": "before", "direction": "outbound"},
    "earliest": "07:00", "deadline": "08:10", "min_travel_min": 12,
    "poll_start": "06:30", "poll_end": "09:30"
  }
}
```

Find stop ids with `GET /v1/tfl/stations?q=...&modes=bus`. Keep your own config out of the repo.

* **`alight_return`**: some buses terminate at the target and drop off on the opposite side.
  Such a bus counts if it was seen at `before_alight` shortly before.
* **`place`**: stops with the same place name are the same place on opposite sides. A bus seen at
  both one after the other has turned round there; this is stored as a `turnaround` anomaly with
  both times and the gap.
* Route stop order is read from TfL, or given in `sequences` to override.

## Read

Open `/bus` in a browser for all of this on one page (token typed in, as on `/fuel`).

| Endpoint | Shows |
| --- | --- |
| `/v1/bus/trackers/{id}/live` | buses followed now; next arrivals at each stop |
| `/v1/bus/trackers/{id}/target?day=` | scheduled slots, buses that made it, the last one, a spoken summary |
| `/v1/bus/trackers/{id}/reliability` | per-slot hit rate over 30 days |
| `/v1/bus/trackers/{id}/conditions` | leg times, last 30 minutes vs the day |
| `/v1/bus/trackers/{id}/anomalies` | trip starts, reverse moves, turnarounds |

Polling runs every 30 s inside the poll window (`WA_BUS_POLL_ENABLED`, `WA_LOCAL_TZ`).

## Home Assistant (MQTT)

The worker's Home Assistant publisher (every five minutes, with fuel, trains and the rest) turns each
enabled tracker into a device "Bus tracker {id}", with discovery, so these sensors appear on their own.
The day's answer comes from the stored results, so it is still there, and kept up to date, after the
polling window closes; the live values (buses being followed, next arrivals) are only asked for while the
tracker is polling and show 0 / unknown outside it.

| Sensor | State |
| --- | --- |
| `last_bus` | time the last bus reached the target before the deadline (`none` if not yet) |
| `hits_today`, `pending_slots` | buses confirmed, scheduled buses not yet seen |
| `active_buses` | buses being followed (while polling) |
| `summary` | one-sentence answer, good for voice |
| `next_{stop key}` | minutes to the next bus at each stop of interest (attributes: next three; while polling) |

Settings: `WA_MQTT_HOST`, `WA_MQTT_PORT`, `WA_MQTT_USER`, secret `mqtt_password`,
`WA_MQTT_ENABLED`, `WA_MQTT_TOPIC_PREFIX` (default `world_assistant`),
`WA_MQTT_DISCOVERY_PREFIX` (default `homeassistant`). If the broker is unreachable, publishing
backs off for five minutes and polling carries on.
