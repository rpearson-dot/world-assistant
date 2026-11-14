# Discovery (`discovery`)

What each device on your network probably is (its adapter's maker, its name, what it looks up) and the module that would suit it.

Reads what the Network module has seen and the IEEE register of adapter makers. Changes nothing

| | |
|---|---|
| Role | Processor: works from other data |
| Reads | the IEEE register of MAC address prefixes (weekly) |
| Gives | what each device probably is, and the module that would suit it |
| Needs | [network](../network/) |
| Endpoints | `/v1/discovery`, `/discovery` |
| Scheduled jobs | `discovery_oui` |
| Assistant tools | `what_is_on_the_network` |
| Plugs into | `assistant.tools` |

## Files

- `api.py`: Discovery (/v1/discovery, In the home → Discovery): what each device on your network probably is, and the module that would suit it. Reads what the Network...
- `identify.py`: What a device on your network probably is, and what World Assistant could do with it. Pure.
- `page.py`: The discovery page (/discovery, In the home → Discovery): what each device probably is and what World Assistant could do with it. The page holds no data and...
- `tools.py`: The assistant's tool for this module, served with the rest over MCP (/mcp) and to Siri (/v1/ask).

_Written by `scripts/module-readmes.py` from the module's declaration (app/modules/builtin.py) and its files; run it after changing either._
