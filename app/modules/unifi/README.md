# UniFi gateway log (`unifi`)

Devices joining and leaving the network and the gateway's firewall decisions, from the UniFi gateway's own log (syslog): the Ubiquiti page ranks what each device reaches and suggests firewall blocks for chatty devices (advisory — World Assistant never changes your firewall).

The UniFi gateway sends its log to this server (UDP 5514 unless WA_SYSLOG_PORT says otherwise): Settings → CyberSecure → Traffic Logging → Activity Logging, SIEM server. Nothing goes to Home Assistant

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | the UniFi gateway's syslog (UDP, from the home network) |
| Gives | devices joining and leaving, firewall decisions, and advisory firewall-block suggestions on the Ubiquiti page |
| Needs | [network](../network/) |
| Endpoints | `/unifi` |
| Assistant tools | `chatty_devices` |
| Plugs into | `assistant.tools` |

## Files

- `__init__.py`: The UniFi gateway (Ubiquiti) side of networking: the firewall picture and advisory block suggestions. The data lives in the network module; this module adds...
- `page.py`: The Ubiquiti page (/unifi, In the home → Networking → Ubiquiti): the UniFi gateway's firewall picture — which devices reach the most outside hosts, what...
- `tools.py`: The assistant's tool for the Ubiquiti side: which devices talk to the most outside hosts, from the UniFi gateway's firewall log, to spot a chatty IoT...

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._
