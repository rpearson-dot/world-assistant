# Tax and MOT (`vehiclechecks`)

Each vehicle's tax and MOT status and due dates, its MOT history with advisories, and outstanding safety recalls (DVLA and DVSA), with reminders.

DVLA: apply for a Vehicle Enquiry Service key. DVSA: register at documentation.history.mot.api.gov.uk; its email gives the API key, client id and secret, and the token URL. Either works alone. Give each vehicle its registration on the Vehicles page.

How to use it: [docs/data-sources.md](../../../docs/data-sources.md).

| | |
|---|---|
| Role | Source: fetches from outside |
| Reads | DVLA Vehicle Enquiry Service; DVSA MOT history |
| Gives | Home Assistant: tax and MOT |
| Keys | `dvla_api_key`, `dvsa_mot_api_key`, `dvsa_mot_client_id`, `dvsa_mot_client_secret`, `dvsa_mot_scope_url`, `dvsa_mot_token_url` |
| Endpoints | `/v1/vehicle-checks` |
| Scheduled jobs | `vehicle_check` |
| Assistant tools | `vehicle_checks` |
| Plugs into | `assistant.tools` |

## Files

- `tools.py`: The assistant's tools for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).
- `vehiclecheck.py`: Tax and MOT for your vehicles: DVLA's Vehicle Enquiry Service and DVSA's MOT History API.

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._
