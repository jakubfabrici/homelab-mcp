# Resilience & recovery

How the live deployment survives the loss of other guests, and how to
rebuild it from nothing. Verified against the real homelab on 2026-09-02.

## What the MCP access path actually depends on

```
Claude app / Claude Code
   │ HTTPS
   ▼
Cloudflare DNS (fabrici.xyz)  ── A record kept fresh by inadyn on the
   │                             UniFi router (192.168.1.1), not by any guest
   ▼
Router port-forward 443 → NPM (LXC 104, 192.168.1.213)
   │
   ▼
mcp CT (LXC 120, static 192.168.1.250:8787)  ← homelab-mcp lives HERE
   ├─ Proxmox API token  → pve (192.168.1.200)
   ├─ own SSH key        → /etc/homelab-mcp/id_ed25519, authorized on all hosts
   └─ HA long-lived token → https://192.168.1.102:8123
```

**Not** in the path: `FABRICI-HOME-SERVER` (LXC 106, 192.168.1.115). That
container only runs claude-rc / HomelabHero UI (`code.fabrici.xyz`,
`kliky.fabrici.xyz`); removing it does not affect MCP access to the homelab.

The single point that matters is **LXC 120** itself. It is deliberately
self-contained: own 8 GB disk, static IP, `onboot: 1`, all secrets in
`/etc/homelab-mcp/` (config, env file, SSH keypair).

## Safeguards in place

- `pct set 120 --protection 1` — `pct destroy 120` refuses until the flag is
  cleared (`pct set 120 --protection 0`), so the MCP container cannot be
  removed by accident while cleaning up other guests.
- A weekly vzdump job (Sunday 03:30, `local` storage, zstd, keep-last=3) backs
  up the whole container including `/etc/homelab-mcp/`. Restore with
  `pct restore 120 /var/lib/vz/dump/vzdump-lxc-120-<stamp>.tar.zst`.

## Rebuilding from scratch (no backup)

1. New Debian LXC, static IP `192.168.1.250/24`, `onboot: 1`; then follow
   [`SETUP.md`](SETUP.md) (installer, Proxmox token, SSH key distribution).
2. NPM already proxies `mcp.fabrici.xyz → 192.168.1.250:8787`; keeping the
   same IP means no proxy change.
3. Home Assistant token: create one in the HA UI (Profile → Security), **or**
   headless over SSH — every HA refresh token stores its `jwt_key` in
   `/config/.storage/auth`, and a long-lived access token is just
   `HS256-JWT({iss: <refresh_token_id>, iat, exp}, jwt_key)`, so a fresh
   access token can be minted for an existing long-lived entry (client_name
   `mcp`) without touching the UI. Put it in `HA_TOKEN=` in
   `/etc/homelab-mcp/homelab-mcp.env` (no `<paste>` placeholders — the module
   fails 401 until a real token is set) and `systemctl restart homelab-mcp`.

## Known couplings that remain

- **NPM (LXC 104)** terminates TLS for `mcp.fabrici.xyz`; if it dies, so does
  remote access. It is unrelated to LXC 106 but is worth its own backup.
- **Router** does DDNS + the 443 port-forward.
- Restarting `homelab-mcp` from *inside* an MCP session cuts that session's
  own connection for a few seconds; expect one failed call and retry.
