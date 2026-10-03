# homelab-mcp

An [MCP](https://modelcontextprotocol.io) server that gives an assistant
(Claude Code, the Claude app, or any MCP client) operational access to a home
lab through **one authenticated HTTPS endpoint**:

- **Proxmox VE** — list nodes and every VM/LXC (discovered live, so new guests
  appear automatically), start/stop/reboot, edit guest config, snapshots,
  storage, run commands inside guests (`pct exec` / QEMU guest agent), and a
  raw API escape hatch.
- **Home Assistant** — read states, call services, drive automations, browse the
  area/device/entity registries (incl. ESPHome devices), history, logbook,
  error log, plus raw REST and WebSocket escape hatches.
- **ESPHome** — list nodes, read/edit YAML, validate and flash over OTA, via the
  dashboard add-on or over SSH.
- **Shell & Docker** — run commands, read/write files, manage systemd units and
  journals, and manage Docker/compose on any host over SSH.
- **Fully Kiosk** — control Fully Kiosk Browser tablets over the Remote Admin
  API: screenshot, load a URL, screen on/off, brightness, text-to-speech,
  restart, and a raw command escape hatch.
- **Network** — ping sweep, TCP port scan and ARP/neighbour tables to discover
  what is on the LAN.

Call `homelab_overview` first — it reports which backends are configured and
gives a live inventory of each.

## How it fits together

```
Claude app / Claude Code
        │  HTTPS + Bearer token
        ▼
  mcp.fabrici.xyz   (edge reverse proxy: Caddy on LXC 116, see homelab/caddy/)
        │  http://127.0.0.1:8787/mcp
        ▼
   homelab-mcp   ──► Proxmox API (token)
   (LXC/VM/Docker)──► Home Assistant (REST + WS)
        │        ──► ESPHome dashboard
        └────────►  SSH to hosts, network scans
```

The server speaks **streamable HTTP MCP** and enforces a bearer token (and an
optional IP allowlist) itself, so it is safe to expose behind your existing
reverse proxy alongside `ha.fabrici.xyz`.

## Metrics

The server exposes Prometheus metrics at `GET /metrics` (own registry, plus
the standard `process_*` / `python_*` collectors):

| metric | labels | meaning |
|---|---|---|
| `homelab_mcp_info` | `version` | build info, always 1 |
| `homelab_mcp_module_enabled` | `module` | 1 when proxmox / homeassistant / esphome / fullykiosk / ssh is configured |
| `homelab_mcp_tool_calls_total` | `tool`, `outcome` | tool invocations, `outcome` is `ok` or `error` |
| `homelab_mcp_tool_duration_seconds` | `tool` | histogram of tool wall-clock time |
| `homelab_mcp_tool_in_flight` | `tool` | tools currently running |
| `homelab_mcp_http_requests_total` | `method`, `path`, `status` | HTTP requests; `path` is normalised to `/mcp`, `/health`, `/metrics` or `other` |
| `homelab_mcp_auth_rejections_total` | `reason` | requests refused by the auth middleware (`ip`, `token`, `metrics`) |
| `homelab_mcp_audit_events_total` | `event` | audit-log events (every state-changing tool records one) |

Labels only ever carry names the server defines (registered tool names,
route names); tool arguments, hosts and commands never appear in metrics.

`/metrics` is **not** open: a scraper gets in when its IP is listed in
`server.metrics_allowed_ips`, or when it sends `Authorization: Bearer
<server.metrics_token>` (a dedicated read-only token, so Prometheus never
holds the master `auth_token`). The master token works there too.

```yaml
server:
  metrics_allowed_ips: ["192.168.1.230/32"]   # the Prometheus host
  metrics_token: ${MCP_METRICS_TOKEN:-}       # optional alternative
```

Prometheus scrape config (IP allowlist variant):

```yaml
- job_name: homelab-mcp
  static_configs:
    - targets: ["192.168.1.250:8787"]
```

The full homelab monitoring stack (Prometheus, Alertmanager, Grafana,
exporters for Proxmox, Home Assistant, every host) lives in
[`monitoring/`](https://github.com/jakubfabrici/homelab/tree/main/monitoring) in the
`homelab` repository and is documented in
[`docs/MONITORING.md`](https://github.com/jakubfabrici/homelab/blob/main/docs/MONITORING.md) there.

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .

cp config/homelab.example.yaml config/homelab.yaml
$EDITOR config/homelab.yaml            # fill in hosts, tokens, ssh key

export MCP_AUTH_TOKEN=$(openssl rand -hex 32)   # or put it in the file
homelab-mcp --check                    # validate config, list modules
homelab-mcp                            # serve on :8787/mcp
```

Every module is optional: leave a section blank and its tools report that they
are not configured instead of failing the whole server.

## Deployment

The intended target is a small LXC container on Proxmox. See
[`deploy/`](deploy/) for a systemd unit, an install script and the edge
reverse proxy ([`caddy/`](https://github.com/jakubfabrici/homelab/tree/main/caddy) in the `homelab` repository — Caddy, which serves every
`*.fabrici.xyz` host; the older nginx / Nginx Proxy Manager snippets are kept
for reference). [`docs/SETUP.md`](docs/SETUP.md) walks
through provisioning the Proxmox API token, the Home Assistant token, the SSH
key and the `mcp.fabrici.xyz` proxy entry end to end.
[`docs/RESILIENCE.md`](docs/RESILIENCE.md) documents what the access path
depends on, the safeguards on the container (protection flag, weekly vzdump)
and how to rebuild or re-mint credentials from scratch.

## Connecting a client

Add to the Claude app / Claude Code as a remote MCP server:

- **URL**: `https://mcp.fabrici.xyz/mcp`
- **Header**: `Authorization: Bearer <MCP_AUTH_TOKEN>`

## Security

The user of this repo chose **full access** (unrestricted shell, all write
tools). That is powerful: the bearer token is effectively root on the whole
network. Accordingly the server:

- requires a bearer token on every request (`hmac.compare_digest`);
- **fails closed**: refuses to start with neither a token nor an allowlist on a
  non-loopback bind, unless `server.insecure: true` is set explicitly;
- supports an IP allowlist; `X-Forwarded-For` is honoured only from trusted
  proxies, and then the *right-most untrusted* hop is used, so the allowlist
  cannot be spoofed by prepending a header (note: a LAN-only allowlist also
  blocks the Claude app connector — see `docs/CLIENT.md`);
- writes a redacted JSONL **audit log** (owner-only, `0600`) of every
  state-changing call, and scrubs registered secrets from error messages too;
- keeps timestamped backups when overwriting files;
- can optionally refuse a few catastrophic commands (`guard_destructive: true`,
  off by default) — a **best-effort** fat-finger backstop, not a security
  boundary: every configured client already holds full shell access.

Secrets never go in git: `config/homelab.yaml`, `.env` and SSH keys are
git-ignored, and config values can be pulled from environment variables.
