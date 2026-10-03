# Nginx Proxy Manager (NPM) setup for mcp.fabrici.xyz

> **Historical.** NPM (LXC 104) was retired in September 2026 and the
> Traefik + NetBird proxy that followed it on 2026-10-01. The live edge proxy
> is **Caddy on LXC 116** (`192.168.1.213`), see [`caddy/`](caddy/). Keep this
> page only as a reference for an NPM-based setup.

Your NPM runs on LXC 104 (`192.168.1.213`) and already terminates every
`*.fabrici.xyz` host, so add the MCP endpoint the same way.

> **Heads-up (from the live audit):** NPM currently has 22 proxy hosts and **no
> access lists**, so everything it publishes is open to the whole internet — the
> app's bearer token is then the *only* thing standing in front of full network
> access. Because of that, put an **Access List** on this host (step 4) and use
> a strong `MCP_AUTH_TOKEN`. Consider adding one to the other sensitive hosts
> too, and dropping the direct WAN forwards to `:8006`/`:8123`.

## 0. DNS

Point `mcp.fabrici.xyz` at the same public IP / CNAME your other
`*.fabrici.xyz` records use, so it lands on NPM.

## 1. Proxy host

**Hosts → Proxy Hosts → Add Proxy Host → Details**

| Field | Value |
|-------|-------|
| Domain Names | `mcp.fabrici.xyz` |
| Scheme | `http` |
| Forward Hostname / IP | the MCP container's LAN IP (e.g. `192.168.1.250`) |
| Forward Port | `8787` |
| Cache Assets | off |
| Block Common Exploits | **on** |
| Websockets Support | **on** |

## 2. SSL

**SSL** tab → Request a new **Let's Encrypt** certificate → enable **Force SSL**
and **HTTP/2 Support**. (Same email/agreement as your other hosts.)

## 3. Advanced

**Advanced** tab → paste, so streaming MCP responses are not buffered and the
real client IP is forwarded:

```nginx
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_buffering off;
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

## 4. Access List (recommended)

**Access Lists → Add** → give it a name (e.g. `mcp`). Two complementary options:

- **Authorization** — add an HTTP Basic user as a cheap second gate in front of
  MCP (optional; the app already enforces the bearer token).
- **Access** — if you only ever call MCP from home / VPN, allow just
  `192.168.1.0/24` and `100.119.0.0/16` (your NetBird overlay) and `Deny All`
  the rest. Leave it open only if you need to reach it from arbitrary networks.

Then set this Access List on the proxy host (Details tab → Access List).

## 5. Tell the app to trust NPM's forwarded IP

In `/etc/homelab-mcp/homelab.yaml`, `server.trusted_proxies` must include NPM's
IP so the app honours its `X-Forwarded-For` (used by the optional
`server.allowed_ips` allowlist). The shipped config already lists
`192.168.1.0/24`, which covers `192.168.1.213` — no change needed unless you
narrow that range.

## 6. Verify

```bash
TOKEN=$(grep MCP_AUTH_TOKEN /etc/homelab-mcp/homelab-mcp.env | cut -d= -f2)
curl -s https://mcp.fabrici.xyz/health          # -> ok
curl -s -X POST https://mcp.fabrici.xyz/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

A `200` with an `initialize` result means NPM → MCP is wired correctly. Then add
the connector per [`../docs/CLIENT.md`](../docs/CLIENT.md).
