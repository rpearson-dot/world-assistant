# Home energy planner

Energy → **Planner** (`/planner`). Turn it on in Settings → Modules. It needs Home Assistant's address and token
(Settings → Home Assistant); the Octopus, Weather, OCPP and OVMS modules feed it when they are on.

## Charging, in shadow

Every five minutes the planner decides what it would do with the car, keeps that decision with what it was based on,
and sets it beside Home Assistant's plan (`sensor.wa_car_charging_plan`) at the same moment. The page shows the latest
of each, how often they agreed this week, and each time they differed.

The decision, in order:

1. The master switch: **Off** means off, **On** means charge whenever plugged in.
2. Not plugged in: unplugged.
3. At or above the target (`input_number.wa_ev_target_soc`): full.
4. A Free Electricity hour you joined, below the "low" level with "always charge when low" on, or the cheap rate:
   charge.
5. **Solar to spare:** the house battery at 98% or more and the house sending at least 3.5 kW to the grid for two
   checks running (or, once charging, still at least 2.5 kW spare counting what the car takes): charge. The charger
   has no current setting from here, so it starts only when the spare covers most of its 4.6 kW.
6. Otherwise: wait. The planner **never charges the car from the house battery**.

Differences are expected where Home Assistant does something on purpose that the planner does not (charging from the
house battery above a level, resting after full). The rest show where one of them is wrong.

For solar to spare, set the house battery entity and the grid power entity in Settings on the page (and whether
export is a negative number, as most inverters report it).

### Letting it act

With **act** ticked, the charger on World Assistant's OCPP and the master switch on **Auto**, the planner starts and
stops the charger itself. While it acts, Home Assistant's start and stop are set aside (the activity log says so), so
the two never fight. The master switch, in World Assistant or Home Assistant, still overrides everything. Untick
**act** to hand control back.

## How predictable are your evenings?

From Home Assistant's long-term statistics (the house's total use, a kWh running total such as the inverter's load
energy) and the Weather module's history, the planner fits each evening's use (16:00 to 19:00 by default) against how
cold it was (heating degree-hours below 15.5 °C). R² says how much of the difference between evenings the cold
explains: 0.6 and above is predictable enough to act on, 0.3 to 0.6 partly, below that not.

It then gives tomorrow evening's likely use from the forecast, the figure 9 evenings in 10 stay under, and the figure
it is very likely to reach. That last figure is the one to judge a standard-rate top-up of the house battery by: only
energy that is very likely to be used in the dear hours is worth buying early.

Worked out each morning at 06:43, and at most every six hours on the page.

## API

| | |
|---|---|
| `GET /v1/planner/charging?days=3` | the latest decision, agreement and differences |
| `GET /v1/planner/demand?fresh=false` | the evening-demand fit and tomorrow |
| `GET`/`PUT /v1/planner/settings` | the statistic, evening, entities, solar thresholds and `act` |

Assistant tools: `charging_plan`, `evening_demand`.
