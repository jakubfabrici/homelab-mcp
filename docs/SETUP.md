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
git clone -b claude/mcp-server-proxmox-e98e5k https://github.com/jakubfabrici/claudecode.git
cd claudecode
bash deploy/install.sh
```

The installer creates a `homelab-mcp` service user, a virtualenv in
`/opt/homelab-mcp`, config in `/etc/homelab-mcp/`, a fresh `MCP_AUTH_TOKEN`, and
an SSH keypair at `/etc/homelab-mcp/id_ed25519`.

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

## 8. Connect the client

See [`CLIENT.md`](CLIENT.md).

## Updating

```bash
cd /opt/homelab-mcp && git pull && .venv/bin/pip install -e . && systemctl restart homelab-mcp
```
(or re-run `deploy/install.sh`).
