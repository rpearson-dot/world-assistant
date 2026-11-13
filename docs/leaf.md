# The Leaf dashboard

A Home Assistant dashboard for the 2016 Nissan Leaf (HV66BZD), fed by
[OVMS](https://www.openvehicles.com/) over MQTT. It needs nothing from World Assistant.

The car reports about 120 things. Almost none of them change what you do next, so the first view
carries only what does, and the rest sits on a second view you look at once a month.

## Putting it in

Settings → Dashboards → Add dashboard → New dashboard from scratch, then open its raw configuration
editor (⋮ → Edit in YAML) and paste [`homeassistant/dashboards/leaf.yaml`](../homeassistant/dashboards/leaf.yaml).

## Before you go

The card at the top of the first view is silent unless something wants doing, so a glance is enough.
It speaks up when:

| It says | When |
| --- | --- |
| 🔌 Plugged in but not charging | the charge port is open and nothing is flowing, with the substate so you can tell a waiting timer from a fault |
| 🪫 Charge is _n_% | below 30% and not charging, with the miles left |
| 🔓 Unlocked | the vehicle lock is unlocked |
| 🚪 … open | any door or the boot is open |
| ⚠️ 12V battery at _n_V | the alert is on, or it is under 11.9V while nothing is charging it |
| ⚠️ Cell voltage / temperature alert | the pack alerts are on |
| ⚠️ _any other warning_ | any other warning OVMS reports is on (a binary sensor of class problem or safety), by its name, so new ones appear without editing the card |
| 🅿️ Handbrake off while parked | the handbrake has been off for 5 minutes with the car standing still (an applied handbrake is normal, so it is not shown) |
| 🛞 … tyre _n_ psi | a tyre is under 33 or over 40 psi, against the 36 recommended |
| 📡 Nothing from the car for _n_ | no report from OVMS for 15 minutes while the car is away (no mobile signal where it is, or its SIM), or for an hour at home: everything else on the page is then its last values |

Otherwise it says `Nothing to do.`, plus the charge and range, or the time to full when charging.

Under that, `Last heard from the car n min ago` says how fresh the page is. It is the newest report from any of
the car's entities: Home Assistant notes every message from OVMS, even one repeating a value, so a parked car
with nothing changing still counts as heard.

A missing or unavailable sensor never raises a warning: a car asleep says nothing rather than
crying wolf. The thresholds are plain numbers in the template, so change them in place.

## Warnings from the car

Home Assistant files some of the car's sensors under its diagnostics, where they are easy to miss. The Health view's
**Warnings from the car** card lists every warning OVMS reports: ✅ OK or ⚠️ needs attention, and the handbrake as
applied or off. Home Assistant shows an applied handbrake as "Problem" because of the sensor's class; it is not one.

## The trail on the map

The map shows the last 24 hours of positions as a coloured trail, in whatever colour Home Assistant
has given the car. It is where the car has been, not anything about the road. Delete `hours_to_show`
from the map card for the current position alone.

## What was dropped

Still recorded and still in the history, just not on a card: motor RPM, inverter power and
temperature, BMS balancing and internal temperature, pack and min cell voltage on the front view,
air intake and vents, cabin setpoint and fan, range change speed, charge mode, charge limit reason,
and the speed readout.

## Listing the entities

To see everything the car exposes, in Developer Tools → Template:

```jinja
{% for s in states if 'hv66bzd' in s.entity_id %}{{ s.entity_id }} = {{ s.state }}
{% endfor %}
```

Match on the number plate, not on `leaf` or `ovms`: several entities carry neither.
