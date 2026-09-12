#!/usr/bin/env bash
# Install homelab-mcp into an LXC container (Debian/Ubuntu) as a systemd service.
# Run as root inside the container:  bash deploy/install.sh
set -euo pipefail

APP_DIR=/opt/homelab-mcp
CFG_DIR=/etc/homelab-mcp
LOG_DIR=/var/log/homelab-mcp
REPO_URL="${REPO_URL:-https://github.com/jakubfabrici/claudecode.git}"
BRANCH="${BRANCH:-claude/mcp-server-proxmox-e98e5k}"

echo ">> installing system packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl openssh-client iputils-ping iproute2 procps

echo ">> creating service user and directories"
id homelab-mcp &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin homelab-mcp
mkdir -p "$APP_DIR" "$CFG_DIR" "$LOG_DIR"

# Source can come from a local directory (SRC_DIR) so a private repo does not
# need GitHub credentials on this machine; otherwise fall back to git clone.
if [ -n "${SRC_DIR:-}" ]; then
  echo ">> copying source from $SRC_DIR into $APP_DIR"
  mkdir -p "$APP_DIR"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude '.venv' --exclude '.git' "$SRC_DIR"/ "$APP_DIR"/
  else
    cp -a "$SRC_DIR"/. "$APP_DIR"/
  fi
elif [ -d "$APP_DIR/.git" ]; then
  echo ">> updating existing checkout in $APP_DIR"
  git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH" && git -C "$APP_DIR" checkout -f "$BRANCH" && git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
  echo ">> cloning $REPO_URL into $APP_DIR"
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

echo ">> creating virtualenv and installing"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q "$APP_DIR"

echo ">> config"
[ -f "$CFG_DIR/homelab.yaml" ] || cp "$APP_DIR/config/homelab.example.yaml" "$CFG_DIR/homelab.yaml"
if [ ! -f "$CFG_DIR/homelab-mcp.env" ]; then
  cat > "$CFG_DIR/homelab-mcp.env" <<ENV
# Secrets for homelab-mcp (referenced as \${VAR} from homelab.yaml)
MCP_AUTH_TOKEN=$(openssl rand -hex 32)
PROXMOX_TOKEN_SECRET=changeme
HA_TOKEN=changeme
ESPHOME_TOKEN=
ENV
  echo "   wrote $CFG_DIR/homelab-mcp.env with a fresh MCP_AUTH_TOKEN"
fi

echo ">> ssh key for reaching homelab hosts"
[ -f "$CFG_DIR/id_ed25519" ] || ssh-keygen -t ed25519 -N "" -C "homelab-mcp" -f "$CFG_DIR/id_ed25519"

chown -R homelab-mcp:homelab-mcp "$APP_DIR" "$CFG_DIR" "$LOG_DIR"
chmod 600 "$CFG_DIR/homelab-mcp.env" "$CFG_DIR/id_ed25519"

echo ">> allowing the service group to ping (unprivileged ICMP sockets)"
# The unit runs with NoNewPrivileges=, which ignores ping's cap_net_raw file
# capability. Listing the service group in net.ipv4.ping_group_range lets ping
# use an ICMP datagram socket instead, without granting the process CAP_NET_RAW.
MCP_GID=$(getent group homelab-mcp | cut -d: -f3)
mkdir -p /etc/sysctl.d
cat > /etc/sysctl.d/60-homelab-mcp.conf <<SYSCTL
# Let the homelab-mcp service user ping without CAP_NET_RAW (net_ping tool).
net.ipv4.ping_group_range = $MCP_GID $MCP_GID
SYSCTL
if ! sysctl -q -p /etc/sysctl.d/60-homelab-mcp.conf 2>/dev/null \
   && ! echo "$MCP_GID $MCP_GID" > /proc/sys/net/ipv4/ping_group_range 2>/dev/null; then
  echo "   WARNING: could not set net.ipv4.ping_group_range in this container." >&2
  echo "   net_ping will fail until you either make /proc/sys/net writable or" >&2
  echo "   uncomment AmbientCapabilities=CAP_NET_RAW in the systemd unit." >&2
fi

echo ">> installing systemd unit"
cp "$APP_DIR/deploy/homelab-mcp.service" /etc/systemd/system/homelab-mcp.service
systemctl daemon-reload
systemctl enable homelab-mcp

cat <<NEXT

Done. Next steps:
  1. Edit $CFG_DIR/homelab.yaml  (hosts, proxmox token_id, ssh hosts)
  2. Edit $CFG_DIR/homelab-mcp.env  (PROXMOX_TOKEN_SECRET, HA_TOKEN)
  3. Copy the public key to your hosts:
       ssh-copy-id -i $CFG_DIR/id_ed25519.pub root@<host>
     (public key: $(cat "$CFG_DIR/id_ed25519.pub" 2>/dev/null))
  4. sudo -u homelab-mcp $APP_DIR/.venv/bin/homelab-mcp --check
  5. systemctl start homelab-mcp && journalctl -u homelab-mcp -f
  6. Point mcp.fabrici.xyz at http://<this-container>:8787  (see deploy/nginx.conf)

Your MCP bearer token:
  grep MCP_AUTH_TOKEN $CFG_DIR/homelab-mcp.env
NEXT
