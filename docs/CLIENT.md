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
