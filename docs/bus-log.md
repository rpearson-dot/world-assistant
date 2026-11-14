# Bus route log (the 213)

A record of every bus on one route during the school run, made to show exactly what happened, failures included.
It is separate from the [bus tracker](bus-tracker.md): the tracker answers "has my bus made it"; the log keeps
everything TfL and Bus Open Data (BODS) said, several times a minute, so it can be looked at afterwards.

The page is **Buses → Route log** (`/buslog`).

## What is recorded

From 07:00 to 09:00 on school days (the window and route are changeable on the page):

| Every | Asked | Kept |
|---|---|---|
| 10 s | BODS's vehicle positions for the route (`lineRef` plus a box around it) | every bus in every answer, with every field BODS sent |
| 15 s | TfL's predictions for the route (`/Line/213/Arrivals`) | each prediction when it first appears, each time it moves by 15 s or more, and when it is no longer predicted |
| 2 min | TfL's status for the route | the severity, the reason and every disruption |
| once a day | TfL's route both ways (stop order and line), and its timetable from each end | the stops with NaPTAN's positions, and every scheduled journey at every stop, as they stood before the window |

**Every request is kept**, whether it worked, came back empty or failed, with its time, how long it took, what
came back and the answer exactly as it came (compressed). That is how a gap in the data is told apart from a bus
that did not come: the "Every request that day" strips on the page show each minute as answered with data,
answered empty, failed, or not asked. A failed TfL request never makes a prediction "gone"; only an answer
without it does.

API keys are never stored with the requests.

## The map

The route is drawn in red with the stops on it, kept visible at every zoom. Each bus is a red box with its
numberplate and where it is going ("LD72YAH / to Sutton").

- **Tap a stop** for its next hour: the buses TfL expects there, and the timetable.
- **Tap a bus** for its times at every stop to the end of its route, against the timetable: on time (within a minute
  either way), early or late, and by how much. Which timetabled journey a bus is running is worked out from its
  predictions (TfL does not say), so it is the closest journey in its direction.
- Move the time slider (or untick Live) to see any moment of a recorded day the same way.
- **Download schedule now** takes today's route and timetable from TfL straight away, instead of waiting for the
  first recording minute.

## When it records

- **School days:** weekdays in term, outside half terms, holidays and INSET days, and not a bank holiday. The
  calendar starts as Coombe Girls School's 2026/27 dates, and is a table on the page ("School days"): terms,
  holidays, INSET days and notes, each editable, with rows added and removed.
- **Every day (a test):** the tick box on the page records the window on every day, weekends and holidays too.
  Use it for a baseline over a weekend, then untick it.
- **Record now:** records straight away for 15 minutes to 4 hours, whatever the day and time.

The worker checks every minute; recording starts within a minute of switching it on.

## Keys and limits

- **BODS** key (Keys page, `bods_api_key`) is needed for positions. BODS asks consumers not to request more often
  than every 5 seconds; this asks every 10.
- **TfL** works without a key, faster with one (`tfl_app_key`). TfL allows 500 requests a minute per key; this uses
  about 5, through the same limiter as the rest of World Assistant.

## Storage

Everything is in World Assistant's Postgres, in tables split by month (`buslog_*`). Rows are only ever added.
Raw answers are kept for 13 months, then dropped a month at a time by the nightly job. Everything else is kept.
Expect about 30 MB a day of recording, mostly raw answers: about 6 GB over a school year.

## API

| | |
|---|---|
| `GET /v1/buslog/status` | recording now and why, next recording days, today's requests by source |
| `GET`/`PUT /v1/buslog/settings` | route, window, every-day test switch |
| `GET`/`PUT /v1/buslog/calendar` | terms, breaks, INSET days |
| `POST`/`DELETE /v1/buslog/record-now` | `{"minutes": 30}` |
| `GET /v1/buslog/route?day=` | the route as recorded that day |
| `GET /v1/buslog/positions?day=&at=` | each bus's last position before a moment |
| `GET /v1/buslog/polls?day=` | each minute's requests by source and outcome |
| `GET /v1/buslog/predictions?day=&stop=&vehicle=` | TfL's prediction changes |
| `GET /v1/buslog/stop/{stop}?day=&at=` | a stop's next hour: timetable and TfL's expected buses |
| `GET /v1/buslog/vehicle/{vehicle}?day=&at=` | a bus's remaining stops against the timetable |
| `POST /v1/buslog/schedule-now` | take today's route and timetable now |
| `GET /v1/buslog/raw/{poll}` | a request's answer exactly as it came (a download) |
