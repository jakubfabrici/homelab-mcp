# Connecting a client

`homelab-mcp` is a **remote MCP server over streamable HTTP**. Any MCP client
that supports remote servers with a bearer header can use it.

Endpoint and auth:

- **URL**: `https://mcp.fabrici.xyz/mcp`
- **Header**: `Authorization: Bearer <MCP_AUTH_TOKEN>`
  (`grep MCP_AUTH_TOKEN /etc/homelab-mcp/homelab-mcp.env` on the server)

## Claude Code (CLI)

```bash
claude mcp add --transport http homelab https://mcp.fabrici.xyz/mcp \
  --header "Authorization: Bearer <MCP_AUTH_TOKEN>"
```

Or in `~/.claude.json` / a project `.mcp.json`:

```json
{
  "mcpServers": {
    "homelab": {
      "type": "http",
      "url": "https://mcp.fabrici.xyz/mcp",
      "headers": { "Authorization": "Bearer <MCP_AUTH_TOKEN>" }
    }
  }
}
```

## Claude app (Custom Connector)

Settings → Connectors → Add custom connector → Remote MCP:

- URL: `https://mcp.fabrici.xyz/mcp`
- Add header `Authorization: Bearer <MCP_AUTH_TOKEN>`


## A note on `allowed_ips` and the Claude app

If you add this server to the **Claude app** (claude.ai) as a remote connector,
its requests originate from Anthropic's cloud, **not from your LAN**. So a
LAN-only `server.allowed_ips` (e.g. `192.168.1.0/24`) will reject the Claude app
with **HTTP 403**, even with the right token. For use from the Claude app
anywhere, keep `allowed_ips: []` (empty) and rely on the bearer token (optionally
add an HTTP Basic auth Access List at the reverse proxy as a second gate).
`allowed_ips` is the right choice only when every client is on your LAN or a
NetBird/VPN peer (e.g. Claude Code CLI running on such a machine).

## First calls

Ask the assistant to run **`homelab_overview`** — it reports which backends are
configured and reachable and gives a live inventory. From there:

- "list all VMs and containers" → `proxmox_guests`
- "restart the container named nextcloud" → `proxmox_guests` then `proxmox_guest_action`
- "what's the state of the chicken coop" → `ha_states` / `ha_get_state`
- "edit the studna ESP to add a sensor" → `esphome_read` / `esphome_write` / `esphome_validate` / `esphome_flash`
- "what's running on 192.168.1.0/24" → `net_scan`

## Verifying by hand

```bash
TOKEN=<MCP_AUTH_TOKEN>
curl -s https://mcp.fabrici.xyz/health          # -> ok
curl -s -X POST https://mcp.fabrici.xyz/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```
