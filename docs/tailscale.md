# Tailscale: World Assistant on your phone, anywhere

`docker-compose.tailscale.yml` adds a small Tailscale container that puts World Assistant on your tailnet as
its own device, with a real HTTPS certificate:

    https://world-assistant.<your-tailnet>.ts.net

Your phone reaches it from anywhere while the Tailscale app is connected: the web interface (installable as
an app), Siri Shortcuts, and AI assistants over MCP. Nothing is opened to the internet; Funnel stays off.

## Set it up (once)

1. In the [Tailscale admin console](https://login.tailscale.com/admin):
   - **DNS**: turn on **MagicDNS** and **HTTPS Certificates**.
   - **Settings > Keys**: **Generate auth key**. Leave *Reusable* and *Ephemeral* off. If you use ACL tags, give
     it one (for example `tag:world-assistant`) and allow your devices to reach it on port 443.
2. On the server, run `scripts/setup.sh` and answer **y** to Tailscale (it asks for the key without showing it).
   Or by hand: put the key in `tailscale_authkey.txt` in the secrets folder, add `docker-compose.tailscale.yml`
   to `COMPOSE_FILE` in `.env`, then `docker compose up -d`.
3. The device appears in the admin console as `world-assistant`. The key is only used for that first start;
   the device's identity is then kept in `WA_DATA_DIR/tailscale`, so the key can expire.

`WA_TAILSCALE_NAME` changes the name; `WA_TAILSCALE_ARGS` passes extra flags (for example
`--advertise-tags=tag:world-assistant` with an OAuth client secret instead of an auth key).

## Your phone

1. Install the Tailscale app and sign in to the same tailnet.
2. Open `https://world-assistant.<your-tailnet>.ts.net` in Safari (iPhone) or Chrome (Android).
3. **Add to Home Screen** (Safari: Share > Add to Home Screen; Chrome: the menu > Install app). It opens like an
   app, full screen, with its own icon. Over HTTPS it also keeps a copy of the pages, so the app still opens
   when the server is briefly out of reach (the data appears once it is back).

### What needs the token

| From | Web pages | API, MCP, Siri |
| --- | --- | --- |
| Home network | no token | no token |
| Your tailnet, through this container | no token, in a browser signed in to your tailnet | **token** |
| Anywhere else | token | token |

On the tailnet a page's own requests are let in because the browser marks them as coming from this site
(`Sec-Fetch-Site: same-origin`) and Tailscale says which tailnet user sent them. A Siri Shortcut, a script or an
MCP client sends neither, so it needs `Authorization: Bearer <token>` (the token is in `api_token.txt`, or on
the Home Assistant page). `WA_TAILNET_WEB_NO_TOKEN=false` makes the pages ask for the token too.

## Siri Shortcuts

*Get Contents of URL*:

- URL: `https://world-assistant.<your-tailnet>.ts.net/v1/ask/bus?format=text` (any tool from
  [assistant.md](assistant.md); `GET /v1/ask` lists them)
- Method **POST**, Request Body **JSON** (the tool's arguments, or empty)
- Headers: `Authorization` = `Bearer <your token>`

then *Speak Text* with the result. Siri runs it on the phone, so the phone must be on the tailnet.

## AI assistants (MCP)

The MCP endpoint is `https://world-assistant.<your-tailnet>.ts.net/mcp`, with the same bearer token.

- **Claude Code** (on a computer on your tailnet):
  `claude mcp add --transport http world-assistant https://world-assistant.<your-tailnet>.ts.net/mcp --header "Authorization: Bearer <token>"`
- **Claude Desktop and other desktop clients** (on a computer on your tailnet), through the `mcp-remote` bridge,
  in the client's MCP config:

  ```json
  {
    "mcpServers": {
      "world-assistant": {
        "command": "npx",
        "args": ["mcp-remote", "https://world-assistant.<your-tailnet>.ts.net/mcp",
                 "--header", "Authorization: Bearer ${WA_TOKEN}"],
        "env": { "WA_TOKEN": "<your token>" }
      }
    }
  }
  ```

- **Connectors added in a vendor's web or mobile app** are called from that vendor's servers, not from your
  device, so they cannot reach a tailnet-only address. That would need Tailscale Funnel (public internet);
  it is deliberately off here. If you ever turn it on, the token is all that stands in front of the API.

## Troubleshooting

- `docker compose logs wa-tailscale`: "needs login" means the auth key was missing, used or expired; put a
  new one in `tailscale_authkey.txt` and `docker compose up -d --force-recreate wa-tailscale`.
- The address does not load: check MagicDNS and HTTPS Certificates are on, and that the phone's Tailscale
  app is connected. The first HTTPS request can take a few seconds while the certificate is issued.
- Pages ask for the token on the tailnet: the browser must be signed in to the tailnet through the Tailscale
  app (Tailscale passes the user's identity), and the stack must have been started with this file (it tells
  the app which container is its Tailscale front door).
