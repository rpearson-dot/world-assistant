# Network (UniFi and Pi-hole)

**In the home → Network** (`/network`). Turn it on in Settings → Setup. Nothing from it goes to Home Assistant.

## What it shows

- **Every device** the router has seen, by its MAC address, with the name and address the router gave it, when it
  was first and last seen, and today's lookups, blocks and firewall decisions. You can give a device your own name.
- **What each device did:**
  - joining and leaving the Wi-Fi and the access point it used;
  - its DNS lookups through Pi-hole, allowed or blocked;
  - firewall decisions for the rules you log.
- **Your Pi-hole groups** (such as Kids, Adults and IoT), with their devices.
- **A notification** when a device joins for the first time (Settings → Notifications: a new device on your network).

## Suggested blocks

For each device, World Assistant looks only at what **got past your Pi-hole lists** in the last week and suggests
what doesn't serve you. A domain is suggested when:

- a manufacturer's tracker list names it;
- its name says what it's for (telemetry, metrics, analytics, ads, crash reports, logging);
- or the device looks it up around the clock.

What a device needs is never suggested: time, updates, certificate checks and the Wi-Fi sign-in check are shown as
**keep**. Where blocking a domain is known to break something (an app store, casting), the suggestion says so and
is not ticked.

You tick what to block, then either:

- **Copy the list**, to add to Pi-hole yourself; or
- **Add to Pi-hole** (press twice, to confirm), for:
  - **this device only:** it also joins a small group of its own, `wa-<device>`, beside the groups it's already in,
    so your lists keep applying to it;
  - **everything in one of your groups**, such as IoT.

Nothing is moved out of a group. **Undo** takes out only what World Assistant added. This is the only thing the
module changes, so it's listed under "Controls" on the Setup page.

A device that uses its own DNS (not Pi-hole) gets around this. A UniFi firewall rule that logs outbound DNS (port
53) not going to Pi-hole shows it in its timeline.

The manufacturers' lists are [HaGeZi's native tracker lists](https://github.com/hagezi/dns-blocklists) (GPL-3.0),
fetched each week. They're used only to recognise a domain, never added to Pi-hole wholesale.

## Setting it up

1. **The UniFi gateway** (UniFi Network 9 or later; menu names move between versions):
   - Settings → CyberSecure → Traffic Logging → Activity Logging (Syslog) → **SIEM Server**: this server's address
     (such as 10.0.2.29) and port **5514**;
   - Settings → Control Plane → Integrations → the same, for system events;
   - in the Policy Engine, turn on **Syslog Logging** for the firewall rules you want to see. Start with block rules
     and the default deny, not "allow all", which is very chatty.
2. **Pi-hole v6:** Settings → Web interface / API → **Configure app password**. Put it on World Assistant's Keys page
   (Pi-hole app password), and Pi-hole's address (such as `http://10.0.2.29:8080`) in Settings on the Network page.
   **Test Pi-hole** checks both.
3. Keep a device's address the same: a DHCP reservation in UniFi helps Pi-hole's per-address view line up.

The syslog listener is its own small service (`wa-syslog`). It takes lines only from home-network addresses and at
most 5,000 a second. `WA_SYSLOG_PORT` in `.env` moves the host's port.

Events are kept 30 days by default (Settings on the page); each device's daily counts are kept for a year.

## API

| | |
|---|---|
| `GET /v1/network/devices` | every device, today's counts, its Pi-hole groups |
| `GET /v1/network/devices/{mac}` | its timeline and most looked-up domains |
| `GET /v1/network/devices/{mac}/suggestions` | suggested blocks, with why |
| `POST /v1/network/devices/{mac}/block` | add ticked domains to Pi-hole (`group_id` for one of your groups) |
| `POST /v1/network/devices/{mac}/unblock` | undo |
| `GET /v1/network/groups` | your Pi-hole groups and their devices |
| `GET /v1/network/events` | the newest events, by kind and action |
| `GET`/`PUT /v1/network/settings`, `POST /v1/network/pihole/test` | settings, and a test of Pi-hole |
