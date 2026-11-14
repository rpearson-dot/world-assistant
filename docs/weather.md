# Weather (Open-Meteo)

The weather at your home place from [Open-Meteo](https://open-meteo.com/): now, hour by hour for a week, each day in
brief, and the past as it turned out. No key; free for non-commercial use, data under CC BY 4.0 (credited on the page).
The page is **Environment → Weather** (`/weather`).

It is kept for planning heat and solar:

- **Heating degree-hours:** for each hour, how far the temperature is below 15.5 °C (below that a home needs heating),
  added up. Twice the degree-hours is about twice the heat your heat pump has to find. Shown for each whole day and
  for 16:00 to 19:00, the dearest hours.
- **Sunshine:** the day's solar radiation (kWh per m²), for the solar side.
- **The past:** the last two days from the forecast service, and 92 days back from Open-Meteo's archive (which runs
  about five days behind), filled in once and kept.

The worker fetches the forecast every hour (at 7 minutes past).

**Home Assistant:** `sensor.wa_weather_temperature`, `sensor.wa_weather_today_degree_hours`,
`sensor.wa_weather_tomorrow_degree_hours`, `sensor.wa_weather_tomorrow_evening` (°C, 16:00 to 19:00) and
`sensor.wa_weather_tomorrow_evening_degree_hours`.

**API:** `GET /v1/weather` (now, 48 hours, 7 days), `GET /v1/weather/history?days=90` (each past day). The assistant:
"what's the weather tomorrow evening".
