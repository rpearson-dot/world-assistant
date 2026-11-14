# Discovery

In the home → Discovery (`/discovery`, `GET /v1/discovery`) names what each device on your network probably is and
the World Assistant module that would suit it. It needs the Network module, and it reads only what that module has
gathered. It changes nothing: no device, no module, nothing in Pi-hole or Home Assistant.

## How a device is recognised

Each kind of device is a rule (`app/modules/discovery/identify.py`). A rule scores points from what was seen in the
last seven days:

| Seen | Points |
| --- | --- |
| A domain it looks up (Pi-hole), such as `fglair.com` for a Fujitsu air conditioner | 3 |
| The maker of its network adapter (the IEEE register, by the first six digits of its MAC address) | 2, or 1 for makers whose chips are in everything (Espressif, Samsung, Google) |
| The name the router gave it, or the name you gave it, such as `AirStage-Lounge` | 2 |
| A port it uses (the firewall log), such as 9100 for a printer | 1 |

The best rule with 3 or more points is **likely**; with 2, **possibly**; less, not recognised. The page shows why.

A phone or laptop often uses a private address, which hides its maker (the page says so). Its name may still give
it away.

## What it suggests

| The suiting module is | Discovery says |
| --- | --- |
| Off | Turn on *module* for it (Settings → Setup) |
| On, but this device is not set up in it | Set it up in *module*, with the device's address |
| Already looking after it | *module* looks after it |
| None yet | A note (for example, a Tigo CCA could be read by an outside module) |

The kinds it knows today: Fujitsu Airstage air conditioners, EV chargers (World Assistant can be their OCPP server),
OVMS, solar inverter dongles, a Tigo CCA, Home Assistant, Garmin devices, LG and Samsung TVs, Roku, Sonos, Amazon
Echo and Fire, Google Nest and Chromecast, Philips Hue, printers, UniFi devices, NAS boxes, phones, computers and
Pi-hole. More are a rule each: add a `Kind` to `KINDS` with a test.

## The register

The `discovery_oui` job fetches the IEEE MA-L register (`oui.csv`, about 4 MB, public) on Tuesdays at 03:57 and keeps
it in `net_oui`. Until the first fetch, makers are unknown and the page says so.
