# Bus route log (the 213)

A record of every bus on one route during the school run, made to show exactly what happened, failures included.
It is separate from the [bus tracker](bus-tracker.md): the tracker answers "has my bus made it"; the log keeps
everything TfL and Bus Open Data (BODS) said, several times a minute, so it can be looked at afterwards.

The page is **Buses → Route log** (`/buslog`).

## The school run

The top of the page answers "what happened to the buses this morning?" for the journey to school: every timetabled
bus from **Worcester Park** (stop C) towards Kingston in the window, and what happened at **Coombe Girls School**.

| Column | What it is |
|---|---|
| Worcester Park: due / left | The timetable, and when it left |
| Coombe Girls School: due | The timetable (as taken before the window) |
| expected | TfL's last prediction before it got there; its first (the start-of-day expectation) underneath |
| got there | When it got there |
| What happened | Made it (and whether by 08:30); from the other side of the road; turned (where); skipped the school; left the route; not seen; no data |

**Actual and probable.** A time in plain type is when TfL's prediction for that stop ended as the bus got there (TfL
stops predicting a bus at a stop when it arrives). A time in *italics with ≈* is probable: worked out from the bus's
GPS, which comes about once a minute, from where it was either side of the stop. A prediction that ends while the
GPS shows the bus somewhere else (turned, or off the route) is "dropped", never an arrival.

**How a bus is followed.** Each GPS position is placed on the route's line towards Kingston: how far along it is, and
how far from the line.

- **Turned:** 400 m or more back from the furthest point it reached. The place is the last stop it passed: a bus
  that turns after Blakes Lane (it shows Blakes Lane towards Kingston, then Blakes Lane towards Sutton) turned at
  **the A3 roundabout** (St James Church / Kingston By-Pass); one that reaches the Fountain and comes back turned at
  **the Fountain roundabout**.
- **Left the route:** more than 150 m from the line. Where it comes back says what it missed: further along, the
  stops in between were skipped; heading back towards Sutton, it turned off the route.
- **From the other side:** a bus that comes back past the school heading towards Sutton, or that TfL says reached the
  stop on the return side. It counts as having got there (the pupils can get off at the school).
- **Delays:** the first time TfL showed it 5 minutes or more late at any stop, and when.
- **Not seen / no data:** a timetabled journey no bus was seen running. If no positions came in around its time, it is
  "no data" (not known), not "not seen".

Tap a bus for every stop from Worcester Park to Kingston (timetable, TfL's first and last predictions, when it got
there, and notes such as "not reached: turned at the A3 roundabout"), and **Watch it on the map**.

**Playback.** Below the time slider, marks show the morning's moments: green at the school, red turned or left the
route, blue back on the route, amber a delay shown. Tap one to jump to just before it with that bus picked (its last
ten minutes drawn behind it). **Play** runs the morning at 1×, 10×, 30× or 60× (10× shows a two-hour window in 12
minutes; 60× in 2). A recorded day plays from the buses' GPS positions, drawn between fixes.

**Reliability over time.** Each recorded morning: buses recorded, made it (and the percentage), in time, from the
other side, turned, skipped, not seen, with the totals. Days with minutes of no positions are marked with *.
A day is worked out once it is over and kept (`buslog_schoolrun`); the nightly job keeps yesterday's.

**The stops and the time** are set under "Stops and time" (NaPTAN ids; defaults Worcester Park C `490001346C`,
Coombe Girls School `490005569W` and, on the other side, `490005569E`; by 08:30).

**Download this day** gives the day's whole recording (route, timetable, positions, predictions, status, requests)
as one gzip JSON file.

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
| `GET /v1/buslog/schoolrun?day=&fresh=` | the school run: each bus, what happened, each stop's times, the moments to mark |
| `GET /v1/buslog/schoolrun/reliability?days=` | each recorded morning's figures and the totals |
| `GET`/`PUT /v1/buslog/schoolrun/settings` | the first stop, the school's stops (both sides) and the time to be there by |
| `GET /v1/buslog/tracks?day=` | every bus's GPS positions over the window, for playback |
| `GET /v1/buslog/export?day=` | the day's recording as one gzip JSON file |
