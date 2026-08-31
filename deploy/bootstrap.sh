#!/usr/bin/env bash
# One-shot, idempotent bootstrap for homelab-mcp, meant to run ON the Proxmox
# host (pve). It creates a dedicated LXC, installs the server inside it,
# generates the bearer token and a Proxmox API token on the box (secrets never
# leave the machine), wires the SSH key, drops in the config, and starts the
# service. Safe to re-run: existing pieces are detected and reused.
#
#   Usage (as root on pve):
#     REPO_URL=https://github.com/jakubfabrici/claudecode.git \
#     BRANCH=claude/mcp-server-proxmox-e98e5k \
#     bash bootstrap.sh
#
# What it does NOT do (do these yourself):
#   * mint the Home Assistant long-lived token (browser) -> paste into the env
#   * create the mcp.fabrici.xyz proxy host in Nginx Proxy Manager (GUI)
set -euo pipefail

# ---- tunables --------------------------------------------------------------
VMID="${VMID:-120}"
HOSTNAME_="${HOSTNAME_:-mcp}"
BRIDGE="${BRIDGE:-vmbr0}"
IP_CIDR="${IP_CIDR:-192.168.1.250/24}"        # a free static addr (verified free: .250/.251/.254)
GATEWAY="${GATEWAY:-192.168.1.1}"
STORAGE="${STORAGE:-local-lvm}"
TEMPLATE_STORE="${TEMPLATE_STORE:-local}"
DISK_GB="${DISK_GB:-8}"
MEM_MB="${MEM_MB:-512}"
CORES="${CORES:-1}"
REPO_URL="${REPO_URL:-https://github.com/jakubfabrici/claudecode.git}"
BRANCH="${BRANCH:-claude/mcp-server-proxmox-e98e5k}"
PVE_HOST_IP="${PVE_HOST_IP:-192.168.1.200}"
PVE_TOKEN_USER="${PVE_TOKEN_USER:-root@pam}"
PVE_TOKEN_NAME="${PVE_TOKEN_NAME:-mcp-server}"

CT_IP="${IP_CIDR%%/*}"
# Directory of the repo this script lives in, so the container is populated
# from the host's checkout (no GitHub credentials needed inside the LXC).
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
say() { printf '\n\033[1;36m>> %s\033[0m\n' "$*"; }

# ---- 1. LXC ----------------------------------------------------------------
if pct status "$VMID" &>/dev/null; then
  say "LXC $VMID already exists, reusing it"
else
  say "creating LXC $VMID ($HOSTNAME_) at $IP_CIDR"
  TMPL=$(pveam list "$TEMPLATE_STORE" 2>/dev/null | awk '/debian-12.*amd64/{print $1}' | tail -1)
  if [ -z "${TMPL:-}" ]; then
    pveam update
    pveam download "$TEMPLATE_STORE" "$(pveam available | awk '/debian-12-standard.*amd64/{print $2}' | tail -1)"
    TMPL=$(pveam list "$TEMPLATE_STORE" | awk '/debian-12.*amd64/{print $1}' | tail -1)
  fi
  pct create "$VMID" "$TMPL" \
    --hostname "$HOSTNAME_" --cores "$CORES" --memory "$MEM_MB" \
    --rootfs "${STORAGE}:${DISK_GB}" \
    --net0 "name=eth0,bridge=${BRIDGE},ip=${IP_CIDR},gw=${GATEWAY}" \
    --features nesting=1 --unprivileged 1 --onboot 1 --start 1
  sleep 5
fi

# helper: run a command inside the container
inct() { pct exec "$VMID" -- bash -lc "$*"; }

say "waiting for container network"
for _ in $(seq 1 30); do inct "getent hosts deb.debian.org >/dev/null 2>&1" && break; sleep 2; done

# ---- 2. install homelab-mcp inside the container ---------------------------
# Push the host's checkout into the container as a tarball so the LXC never
# needs GitHub access itself (the repo is private).
say "copying source ($REPO_DIR) into the container and installing"
inct "apt-get update -qq && apt-get install -y -qq ca-certificates rsync >/dev/null"
TARBALL="/tmp/homelab-mcp-src.tar.gz"
tar -C "$REPO_DIR" --exclude=.git --exclude=.venv -czf "$TARBALL" .
inct "rm -rf /opt/homelab-mcp-src && mkdir -p /opt/homelab-mcp-src"
pct push "$VMID" "$TARBALL" /tmp/homelab-mcp-src.tar.gz
inct "tar -C /opt/homelab-mcp-src -xzf /tmp/homelab-mcp-src.tar.gz"
inct "SRC_DIR=/opt/homelab-mcp-src bash /opt/homelab-mcp-src/deploy/install.sh"

# ---- 3. Proxmox API token (secret captured on-box) -------------------------
# Idempotent: a Proxmox token secret is shown only once, so only (re)create the
# token when the container does not already hold a real secret. Re-running must
# NOT rotate a working secret out from under the deployed server.
say "ensuring Proxmox API token ${PVE_TOKEN_USER}!${PVE_TOKEN_NAME} with rights"
EXISTING_SECRET=$(inct "grep '^PROXMOX_TOKEN_SECRET=' /etc/homelab-mcp/homelab-mcp.env 2>/dev/null | cut -d= -f2-" || true)
TOKEN_EXISTS=$(pveum user token list "$PVE_TOKEN_USER" --output-format json 2>/dev/null \
  | python3 -c "import sys,json;print(any(t['tokenid']=='${PVE_TOKEN_NAME}' for t in json.load(sys.stdin)))" 2>/dev/null || echo False)
case "$EXISTING_SECRET" in ""|changeme|"\${PROXMOX_TOKEN_SECRET}") HAS_REAL_SECRET=0;; *) HAS_REAL_SECRET=1;; esac

if [ "$TOKEN_EXISTS" = "True" ] && [ "$HAS_REAL_SECRET" = "1" ]; then
  echo "   token already exists and the container holds a secret -> reusing (no rotation)"
  PVE_SECRET="$EXISTING_SECRET"
else
  pveum user token remove "$PVE_TOKEN_USER" "$PVE_TOKEN_NAME" 2>/dev/null || true
  TOKOUT=$(pveum user token add "$PVE_TOKEN_USER" "$PVE_TOKEN_NAME" --privsep 1 --output-format json)
  PVE_SECRET=$(printf '%s' "$TOKOUT" | python3 -c 'import sys,json;print(json.load(sys.stdin)["value"])')
  echo "   fresh token created"
fi
pveum acl modify / --tokens "${PVE_TOKEN_USER}!${PVE_TOKEN_NAME}" --roles PVEVMAdmin,PVEAuditor,PVEDatastoreUser
echo "   token rights granted (PVEVMAdmin,PVEAuditor,PVEDatastoreUser)"

# ---- 4. authorize the container's SSH key back onto pve --------------------
say "authorizing the MCP container's SSH key on pve (for pct exec / host ops)"
PUBKEY=$(inct "cat /etc/homelab-mcp/id_ed25519.pub")
install -d -m 700 /root/.ssh
touch /root/.ssh/authorized_keys
grep -qxF "$PUBKEY" /root/.ssh/authorized_keys || echo "$PUBKEY" >> /root/.ssh/authorized_keys
echo "   pve authorized. Public key to copy to the OTHER hosts:"
echo "   $PUBKEY"

# ---- 5. drop in config + secrets on-box ------------------------------------
say "writing config + env inside the container"
inct "cp -n /opt/homelab-mcp/config/homelab.example.yaml /etc/homelab-mcp/homelab.yaml || true"
# Fill the env file: keep an existing MCP_AUTH_TOKEN, set the Proxmox secret.
inct "grep -q '^MCP_AUTH_TOKEN=' /etc/homelab-mcp/homelab-mcp.env 2>/dev/null || echo MCP_AUTH_TOKEN=\$(openssl rand -hex 32) >> /etc/homelab-mcp/homelab-mcp.env"
pct exec "$VMID" -- bash -lc "python3 - <<'PY'
import re,pathlib
p=pathlib.Path('/etc/homelab-mcp/homelab-mcp.env')
lines=[l for l in (p.read_text().splitlines() if p.exists() else []) if not l.startswith('PROXMOX_TOKEN_SECRET=')]
lines.append('PROXMOX_TOKEN_SECRET=${PVE_SECRET}')
if not any(l.startswith('HA_TOKEN=') for l in lines): lines.append('HA_TOKEN=PASTE_HA_LONG_LIVED_TOKEN_HERE')
p.write_text('\n'.join(lines)+'\n'); import os; os.chmod(p,0o600)
print('env updated:', [l.split('=',1)[0] for l in lines])
PY"

# ---- 6. tidy up ------------------------------------------------------------
rm -f "$TARBALL"
inct "rm -f /tmp/homelab-mcp-src.tar.gz" || true

echo
say "DONE (server installed and configured on ${CT_IP})"
cat <<NEXT
Remaining manual steps:
  1. Copy the config produced for you into the container (it has your real
     hosts/IPs), then restart:
       # from wherever you have homelab.yaml:
       pct push $VMID homelab.yaml /etc/homelab-mcp/homelab.yaml
  2. Mint a Home Assistant long-lived token (HA -> profile -> Security) and set
     it in the container:
       pct exec $VMID -- sed -i 's|^HA_TOKEN=.*|HA_TOKEN=<paste>|' /etc/homelab-mcp/homelab-mcp.env
  3. Authorize the printed SSH public key on the other hosts (HA, npm, omv,
     unifi, jellyfin, heimdall, changedetection, qbittorrent):
       ssh-copy-id -i /etc/homelab-mcp/id_ed25519.pub root@<host>
     (or append it to each host's ~/.ssh/authorized_keys)
  4. Start / restart and check (the CLI needs the env file sourced; systemd
     already does this for the running service):
       pct exec $VMID -- systemctl restart homelab-mcp
       pct exec $VMID -- bash -lc 'set -a; . /etc/homelab-mcp/homelab-mcp.env; set +a; /opt/homelab-mcp/.venv/bin/homelab-mcp --check'
       pct exec $VMID -- curl -s http://localhost:8787/health
  5. Add the mcp.fabrici.xyz proxy host in NPM -> forward to ${CT_IP}:8787
     (see deploy/nginx-proxy-manager.md)

Your MCP bearer token (needed by the client):
  pct exec $VMID -- grep MCP_AUTH_TOKEN /etc/homelab-mcp/homelab-mcp.env
NEXT
