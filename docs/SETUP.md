# Setup guide

End-to-end setup of `homelab-mcp` so the Claude app / Claude Code can operate
your Proxmox cluster, Home Assistant and ESPHome devices through
`https://mcp.fabrici.xyz/mcp`.

## 0. Where it runs

Create a small **LXC container** on Proxmox (Debian 12, 1 vCPU, 512 MB RAM,
8 GB disk is plenty) with an IP on your LAN, e.g. `192.168.1.40`. Everything
below happens inside that container unless noted.

> The MCP server itself does not need to run *on* the Proxmox host — it reaches
> Proxmox over the API and the hosts over SSH. A dedicated container keeps its
> blast radius contained and its SSH key isolated.

## 1. Install

```bash
# inside the container, as root
apt-get update && apt-get install -y git
git clone https://github.com/jakubfabrici/homelab-mcp.git
cd homelab-mcp
bash deploy/install.sh
```

The installer creates a `homelab-mcp` service user, a virtualenv in
`/opt/homelab-mcp`, config in `/etc/homelab-mcp/`, a fresh `MCP_AUTH_TOKEN`, and
an SSH keypair at `/etc/homelab-mcp/id_ed25519`.

It also writes `/etc/sysctl.d/60-homelab-mcp.conf`, which lists the
`homelab-mcp` group in `net.ipv4.ping_group_range`. The systemd unit runs with
`NoNewPrivileges=`, which disables the `cap_net_raw` file capability on
`/usr/bin/ping`, so the `net_ping` tool depends on this sysctl to open an
unprivileged ICMP socket instead. See [Troubleshooting](#troubleshooting) if
`net_ping` reports that it cannot open a socket.

## 2. Proxmox API token

In the Proxmox web UI:

1. **Datacenter → Permissions → API Tokens → Add**
   - User: `root@pam` (or a dedicated user)
   - Token ID: `claude`
   - **Uncheck** "Privilege Separation" for full access (your chosen posture),
     or leave it checked and grant the token a role explicitly.
2. Copy the **Token ID** (`root@pam!claude`) and the **secret** (shown once).
3. Put them in the config:
   - `proxmox.token_id: root@pam!claude` in `/etc/homelab-mcp/homelab.yaml`
   - `PROXMOX_TOKEN_SECRET=...` in `/etc/homelab-mcp/homelab-mcp.env`

For `proxmox_guest_exec` into **LXC** containers the server SSHes to the Proxmox
node and runs `pct exec`, so add the node under `ssh.hosts` and set
`proxmox.ssh_host` to that entry. For **QEMU VMs**, install the
`qemu-guest-agent` package inside the VM.

## 3. Home Assistant token

In Home Assistant: **Profile → Security → Long-lived access tokens → Create**.
Put the token in `HA_TOKEN=...` in the env file and set
`homeassistant.url: https://ha.fabrici.xyz`.

## 4. ESPHome

- If the **ESPHome dashboard** add-on is reachable (e.g.
  `http://<ha-ip>:6052`), set `esphome.url` to it.
- If it is not exposed, leave `esphome.url` empty and set
  `esphome.ssh_host` to a host that has the `esphome` CLI and the YAML under
  `esphome.config_dir` (usually the HA host). The server then edits the files
  and runs `esphome` over SSH.
- On **Home Assistant OS** the `esphome` binary is not on the host — it lives
  in the add-on's Docker container. Set `esphome.ssh_host` to the HA host and
  `esphome.docker_container` to the add-on container; the server then runs
  `docker exec <container> esphome ...` and reads/writes the YAML inside it.
  Find the name with:
  `ssh <ssh_host> "docker ps --format '{{.Names}}' | grep esphome"`.

## 5. SSH access to your hosts

Copy the container's public key to every host you want shell/Docker access on:

```bash
cat /etc/homelab-mcp/id_ed25519.pub    # copy this
# on each target host, append it to /root/.ssh/authorized_keys, or:
ssh-copy-id -i /etc/homelab-mcp/id_ed25519.pub root@192.168.1.10
```

List the hosts under `ssh.hosts` in `homelab.yaml`. Any LAN host/IP can also be
addressed ad hoc as long as one host entry supplies the key.

## 6. Validate and start

```bash
sudo -u homelab-mcp /opt/homelab-mcp/.venv/bin/homelab-mcp --check
systemctl start homelab-mcp
journalctl -u homelab-mcp -f
curl -s http://localhost:8787/health          # -> ok
```

## 7. Expose via mcp.fabrici.xyz

Add a reverse-proxy entry next to your existing `ha.fabrici.xyz`:

- nginx: see [`deploy/nginx.conf`](../deploy/nginx.conf)
- Nginx Proxy Manager: see [`deploy/nginx-proxy-manager.md`](../deploy/nginx-proxy-manager.md)

Point it at `http://192.168.1.40:8787`. Get a TLS cert for `mcp.fabrici.xyz`.

## 7b. Prometheus metrics (optional)

The server serves `GET /metrics` for Prometheus. Allow your Prometheus host
in `/etc/homelab-mcp/homelab.yaml` and restart the service:

```yaml
server:
  metrics_allowed_ips: ["192.168.1.230/32"]   # the monitoring LXC
  # or hand Prometheus a dedicated read-only token instead of an IP:
  # metrics_token: ${MCP_METRICS_TOKEN}
```

Check with `curl http://<mcp-ip>:8787/metrics` from the allowed host (anything
else gets 403). The complete monitoring stack is in the `homelab` repository
(`monitoring/`, https://github.com/jakubfabrici/homelab) and
described in [`MONITORING.md`](MONITORING.md).

## 8. Connect the client

See [`CLIENT.md`](CLIENT.md).

## Updating

```bash
cd /opt/homelab-mcp && git pull && .venv/bin/pip install -e . && systemctl restart homelab-mcp
```
(or re-run `deploy/install.sh`).

## Troubleshooting

### `net_ping` fails with "socket: Operation not permitted"

The service user has no raw-socket capability (the unit's `NoNewPrivileges=`
ignores ping's file capabilities), and the kernel is not allowing it an
unprivileged ICMP socket either. Check and fix the sysctl the installer sets:

```bash
cat /proc/sys/net/ipv4/ping_group_range        # "1 0" means nobody may ping
getent group homelab-mcp | cut -d: -f3          # the service group id
sysctl -p /etc/sysctl.d/60-homelab-mcp.conf     # re-apply (re-run install.sh to recreate)
sudo -u homelab-mcp ping -c 1 192.168.1.1       # should print RTTs now
```

If `/proc/sys/net/ipv4/ping_group_range` is read-only in your container, the
fallback is to grant the unit `CAP_NET_RAW` instead: uncomment the
`AmbientCapabilities=CAP_NET_RAW` and `CapabilityBoundingSet=CAP_NET_RAW` lines
in `/etc/systemd/system/homelab-mcp.service`, then
`systemctl daemon-reload && systemctl restart homelab-mcp`. Ambient
capabilities are inherited across `execve` even with `NoNewPrivileges=`, so
`ping` gets the capability; the trade-off is that the whole server process
holds it too. Either way, `net_ping ... via_host=<ssh host>` pings from another
machine and needs neither.
