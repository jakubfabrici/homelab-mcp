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
apt-get install -y -qq python3 python3-venv python3-pip git openssh-client iputils-ping iproute2

echo ">> creating service user and directories"
id homelab-mcp &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin homelab-mcp
mkdir -p "$APP_DIR" "$CFG_DIR" "$LOG_DIR"

echo ">> fetching source into $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH" && git -C "$APP_DIR" checkout -f "$BRANCH" && git -C "$APP_DIR" reset --hard "origin/$BRANCH"
else
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
