# Solar forecast (forecast.solar)

Energy → **Solar** (`/solar`). Turn it on in Settings → Setup. No key or account.

## Setting it up

Under **Your arrays**, add one row for each set of panels facing the same way at the same tilt:

- **Tilt:** degrees from flat (0 is flat, 90 is upright).
- **Direction:** the compass bearing the panels face: 180 south, 90 east, 270 west.
- **kWp:** the array's peak power in kW.

Or press **Copy from Home Assistant**. It reads the arrays from Home Assistant's Forecast.Solar integration, using
the share set on the Home Assistant page. Check them, then Save.

Choose the Home Assistant statistic that is your panels' total output (a kWh running total, such as the inverter's
PV energy) for the accuracy check.

## What it does

- Every hour from 04:11 to 21:11, it asks forecast.solar for each array's forecast for today and tomorrow. The free
  service allows 12 requests an hour from one address, one for each array, so up to 6 arrays leaves room for
  **Fetch now**.
- The page shows the kWh expected today, still to come today, and tomorrow, the power expected now, and each hour.
- Each day's total, as forecast the evening before, is kept and set against what the panels made. "Typically
  X kWh off" says how far to trust tomorrow's figure before anything relies on it.
- Home Assistant sensors: `sensor.wa_solar_today`, `sensor.wa_solar_remaining_today`, `sensor.wa_solar_tomorrow`
  and `sensor.wa_solar_power_now`.
- The Planner tab shows tomorrow's solar next to tomorrow evening's likely use.
- Assistant tool: `solar_forecast`.

## API

| | |
|---|---|
| `GET /v1/solar` | today, still to come, tomorrow, power now, each hour |
| `GET /v1/solar/accuracy?days=14` | each day's forecast the evening before, and what the panels made |
| `GET`/`PUT /v1/solar/settings` | the arrays and the Home Assistant statistic |
| `POST /v1/solar/from-home-assistant` | the arrays set in Home Assistant (not saved) |
| `POST /v1/solar/refresh` | fetch now |
