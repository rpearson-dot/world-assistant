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
* **`either_side`** (default `true`): reaching the stop on either side of the road counts. A bus that
  goes on to `alight_return` shortly (within 20 minutes) after the last stop it was seen at in your
  direction is a success too; it no longer has to be seen at `before_alight` first. Set `false` for the
  strict rule.
* **`show_until`** (default `08:00`, top level): after this time of day on a weekday the chance of making
  the deadline is no longer shown (nothing leaving later could make it), and at weekends it is never shown.
* **`direction`** is only a note of which way the route runs at set-up; matching does not use it, because stops are specific to one side of the road.
* **`place`**: stops with the same place name are the same place on opposite sides. A bus seen at
  both one after the other has turned round there; this is stored as a `turnaround` anomaly with
  both times and the gap.
* Route stop order is read from TfL, or given in `sequences` to override.

## Read

Open `/bus` in a browser for all of this on one page (token typed in, as on `/fuel`).

| Endpoint | Shows |
| --- | --- |
| `/v1/bus/trackers/{id}/live` | buses followed now; next arrivals at each stop |
| `/v1/bus/trackers/{id}/target?day=` | scheduled slots, buses that made it, the last one, a spoken summary, and `outlook`: the chance each remaining bus gets there by the deadline |
| `/v1/bus/trackers/{id}/terminations?days=` | a daily log of buses that ended a trip short of the end of the route while watched: the stop where each was last seen and when, whether it turned back (and where and when it next appeared) or just stopped appearing, and whether that was between your earliest time and the deadline |
| `/v1/bus/trackers/{id}/log?days=` | success or failure of every scheduled bus on every recorded day, with arrival time and delay, and the overall success rate |
| `/v1/bus/trackers/{id}/reliability` | per-slot hit rate over 30 days |
| `/v1/bus/trackers/{id}/conditions` | leg times, last 30 minutes vs the day |
| `/v1/bus/trackers/{id}/anomalies` | trip starts, reverse moves, turnarounds |

Polling runs every 30 s inside the poll window (`WA_BUS_POLL_ENABLED`, `WA_LOCAL_TZ`).

## Backfilling a missed day

If the live matching missed a day (it did, once: see the changelog), run
`docker compose exec worker python -m app.integrations.bus.backfill <tracker> [YYYY-MM-DD]`. It works out
which of the buses the tracker still remembers (its last 40 trips) reached the target in time, using the
same rule, and saves the ones not already saved. Do it the same day: older trips are pushed out of
memory by newer ones. Repeating it is harmless.

## Changing the times

The deadline (08:10 by default), the earliest departure and the time to stop showing the chance are
settings, not fixed. Change them from Home Assistant (below), with `POST /v1/bus/settings` (any of
`deadline`, `earliest`, `show_until`, as `HH:MM`, plus `tracker` when you have more than one) changes only those, without resetting the route, the
timetable or the history; it answers `{"ok", "message"}`. Results already recorded keep the deadline they
were recorded under, so after a change the chance mixes old and new days for a while.

## Buses that end early

While polling, each bus that ends a trip at least two stops short of the end of its route is logged as an
`early_termination` event: **turned back** (the same bus next appears going the other way: the log says
where) or **stopped reporting** (it disappeared from TfL's feed mid-route: out of service, or a gap in
the data). Each has the stop where it was last seen, the time (that stop's expected time, so it can be a
minute or two out) and how many stops short of the end it was. Read it from `…/terminations`.

## How the chance is worked out

Each weekday morning the tracker records which scheduled departures actually reached the target before
the deadline. The chance for a departure is how often that same departure made it over the last 30
days. A departure with fewer than three recorded days uses the rate across all departures instead
(the attributes say which), and with no history there is no number rather than a guess. In Home
Assistant, `chance` uses the departure nearest to the bus that is actually next at your stop. A
departure with no bus is logged as a failure 30 minutes after its scheduled time; until then it is
pending. Success needs the bus to be confirmed at the target by the deadline, on either side of the road.

## Home Assistant (MQTT)

The worker's Home Assistant publisher (every five minutes, with fuel, trains and the rest) turns each
enabled tracker into a device "Bus tracker {id}", with discovery, so these sensors appear on their own.
The day's answer comes from the stored results, so it is still there, and kept up to date, after the
polling window closes. The next arrivals and buses being followed come from TfL's live feed on each publish, at any hour.

| Sensor | State |
| --- | --- |
| `last_bus` | time the last bus reached the target before the deadline (`none` if not yet) |
| `chance` | percent chance the next bus reaches the target by the deadline (unknown when not relevant: weekends, after `show_until`, or no history yet). Attributes: which bus, how many days it is based on, later buses |
| `ended_early` | how many buses ended early today; attributes: today's list (time, kind, stop, where it next appeared) and the last week |
| `record` | percent of scheduled buses over the last 30 days that made it; attributes: counts and the last week's results |
| `hits_today`, `pending_slots` | buses confirmed, scheduled buses not yet seen |
| `active_buses` | buses being followed (while polling) |
| `summary` | one-sentence answer, good for voice |
| `next_{stop key}` | minutes to the next bus at each stop of interest (attributes: next three; while polling) |

To change the times from Home Assistant, add `homeassistant/packages/world_assistant_bus.yaml` to your
packages folder and `wa_bus_settings_url: "http://<nas address>:8010/v1/bus/settings"` to `secrets.yaml`
(with the `wa_authorization` the events package already needs). It gives three time helpers (arrive by,
earliest departure, show the chance until), shown on the Trains and buses dashboard; changing one calls
the API, and a refusal (say, a deadline earlier than the earliest time) appears as a notification. The
helpers are only controls: the times in use are shown in the chance line. Set them once to match.

Settings: `WA_MQTT_HOST`, `WA_MQTT_PORT`, `WA_MQTT_USER`, secret `mqtt_password`,
`WA_MQTT_ENABLED`, `WA_MQTT_TOPIC_PREFIX` (default `world_assistant`),
`WA_MQTT_DISCOVERY_PREFIX` (default `homeassistant`). If the broker is unreachable, publishing
backs off for five minutes and polling carries on.
