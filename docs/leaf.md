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
| 🛞 … tyre _n_ psi | a tyre is under 33 or over 40 psi, against the 36 recommended |

Otherwise it says `Nothing to do.`, plus the charge and range, or the time to full when charging.

A missing or unavailable sensor never raises a warning: a car asleep says nothing rather than
crying wolf. The thresholds are plain numbers in the template, so change them in place.

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
