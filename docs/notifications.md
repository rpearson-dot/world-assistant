# Notifications

World Assistant can send alerts to your phone through its own [ntfy](https://ntfy.sh) server, which you reach over
your tailnet. Set it up and choose what you get under **Settings → Notifications**.

## Setting it up

1. The ntfy server is part of `docker-compose.tailscale.yml`, so it runs if you use Tailscale. It is served on port
   **8443** of World Assistant's own Tailscale device (`https://world-assistant.<tailnet>:8443`), so it needs no new
   auth key. `WA_TAILNET` in `.env` must be your tailnet's name, for example `tail1234.ts.net`, because it is
   part of the server's address.
2. Install the **ntfy** app on your phone (App Store or Google Play). The phone must be on your tailnet, so have the
   Tailscale app connected.
3. In the ntfy app, go to Settings and set **Default server** to the address shown in Settings → Notifications.
   Then subscribe to the topic shown there.
4. Press **Send a test**.

The topic is a long random name, made the first time. Only devices on your tailnet can reach the server, and only
someone who knows the topic can read it. **New topic** makes a new one, and the phone must then subscribe again.

**iPhone:** Apple only lets an app be woken through Apple's push service. The ntfy server therefore sends a wake-up
through `ntfy.sh` (the ntfy project's public server). The wake-up carries only a hash of the topic. The phone then
fetches the message from your server. To turn this off, set `WA_NTFY_UPSTREAM=` (empty) in `.env`. Alerts then
arrive only when the app polls.


## Nothing arrives?

Settings → Notifications → **Check each step**. World Assistant checks, in order: notifications switched on; the
address the phone uses for the server (it must be `https://<device>.<your tailnet>.ts.net:8443`: a wrong or missing
`WA_TAILNET` in `.env` is the usual cause); that it reaches its ntfy server; that the server calls itself by the same
address (an iPhone is woken through ntfy.sh and then fetches the message from that address); that a test message is
accepted; and that the server holds it for the phone. The first failure is the one to fix. The phone itself cannot
be checked from the server: the test message is in the topic, so if the ntfy app shows it but made no sound, it is the
phone's notification settings; if the app does not show it, it is the subscription (the address shown, with
Tailscale on).

## What it sends

| Kind | When | Starts |
|---|---|---|
| **Bike batteries and parts due** | After each Garmin sync: a sensor's or AXS battery reported low or critical, parts that have become due, a sensor seen for the first time | On |
| **Firmware changes** | After each Garmin sync: a device's software version changed (watch, sensors, AXS derailleurs), as read from the activity files. World Assistant cannot tell you that an update is available, only that one was installed | Off |
| **Environment warnings** | Every half hour: a flood warning or severe flood warning at one of your places, air quality high (Defra index 7 or more), very high pollen at home | On |
| **System** | A Garmin sync failing, a Garmin key running out within 14 days, a safety recall naming one of your makers | Off |

Each one is sent once:
- a low battery until it is next charged or changed;
- a due part until it is next replaced or serviced;
- a flood warning until its severity changes;
- air quality and pollen once a day.

Several of the same kind from one sync go in one message.

**Quiet hours** (22:00 to 07:00 to start) hold notifications until the hours end. The server keeps each one and
delivers it then. A severe flood warning is not held.

The last 30 notifications sent, with any failures, are under **Sent lately**.
