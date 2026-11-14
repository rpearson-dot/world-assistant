# Car (OVMS)

World Assistant reads your car's own module ([OVMS](https://www.openvehicles.com/)) from the MQTT broker it already
uses for Home Assistant: battery, range, 12 V battery, charging, locks, doors, position and every notification the
module sends. The car shows on the **Vehicles** page, in its own pane, with any warnings.

Reading only. Locking, unlocking and climate control wait until the broker has TLS: those commands would otherwise
cross your network unencrypted.

## Setting it up

1. The OVMS module already publishes to your Mosquitto broker (OVMS: Config → Server V3 (MQTT)). Nothing changes there.
2. World Assistant uses the broker set in **Settings → Home Assistant**. Its user must be allowed to read the OVMS
   topics (`ovms/#`).
3. Switch the module on (Settings → Modules → Car (OVMS)). Within a minute the car appears on the Vehicles page,
   matched by registration (the OVMS vehicle id, e.g. `HV66BZD`) to the vehicle with that registration.

If the module publishes under a different prefix, set it with `PUT /v1/ovms/settings` (`{"prefix": "ovms/rob/HV66BZD/"}`;
`+` for any part). The default, `ovms/+/+/`, is OVMS's own default for any user and vehicle.

## Warnings

Worked out from the car's metrics, shown on its pane and sent to your phone once each time one starts
("Car, charger and home" notifications):

| Warning | When |
|---|---|
| 12 V battery low | the module's own 12 V alert, with the voltage |
| Alarm sounding | the car's alarm is going off |
| Tyre warning | a tyre pressure or temperature alert |
| Open while parked | a door, the bonnet or the boot, with the car off |
| Unlocked while parked | the car off and not locked |
| Handbrake off while parked | the car off, not moving, handbrake off |
| Battery low | below 15% |
| OVMS quiet | nothing heard for 6 hours (shown, not sent: the car's SIM goes quiet sometimes) |

Every **alert** and **error** notification the module itself sends (charge stopped, charge interrupted, and so on)
goes to your phone too. Info notifications and events are kept and listed on the pane.

## What is kept

Every metric as it stands now; the history of the useful ones (battery, range, 12 V, charging, locks, odometer,
position, signal) when they change, at most once a minute while they move; and every notification and event. Two
years of history.

## API

| | |
|---|---|
| `GET /v1/ovms` | every car: battery, range, 12 V, charging, lock, position, last report, warnings, latest messages |
| `GET /v1/ovms/{id}/metrics` | every metric the car has sent, as it stands |
| `GET /v1/ovms/{id}/history?metric=v.b.soc&days=7` | one metric's history |
| `GET /v1/ovms/{id}/notices` | the car's notifications and events |
| `PUT /v1/ovms/settings` | on or off, topic prefix, which vehicle each OVMS id is |
