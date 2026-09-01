"""Configuration loading for the homelab MCP server.

Configuration comes from a YAML file (``$HOMELAB_MCP_CONFIG`` or
``config/homelab.yaml``).  Any string value may reference an environment
variable with ``${VAR}`` or ``${VAR:-default}`` so secrets can stay out of the
file itself.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

DEFAULT_CONFIG_PATHS = (
    Path("/etc/homelab-mcp/homelab.yaml"),
    Path("config/homelab.yaml"),
)


class ConfigError(RuntimeError):
    """Raised when the configuration is missing or malformed."""


def expand(value: Any) -> Any:
    """Recursively expand ``${VAR}`` references inside strings."""
    if isinstance(value, str):

        def _sub(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            resolved = os.environ.get(name)
            # ${VAR:-default} semantics: the default applies when VAR is unset
            # OR set-but-empty (matching POSIX ':-'), not only when unset.
            if resolved is None:
                if default is None:
                    raise ConfigError(
                        f"environment variable {name!r} referenced in config is not set"
                    )
                resolved = default
            elif resolved == "" and default is not None:
                resolved = default
            return resolved

        return _ENV_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v) for v in value]
    return value


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class ProxmoxConfig:
    host: str = ""
    port: int = 8006
    token_id: str = ""
    token_secret: str = ""
    verify_tls: bool = False
    node: str = ""
    """Optional default node name; when empty every node is queried."""

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.token_id and self.token_secret)

    @property
    def base_url(self) -> str:
        return f"https://{self.host}:{self.port}/api2/json"

    @property
    def ssh_host(self) -> str:
        """Name of the SSH host entry used for ``pct``/``qm`` shell access."""
        return self._ssh_host or "pve"

    _ssh_host: str = ""


@dataclass(slots=True)
class HomeAssistantConfig:
    url: str = ""
    token: str = ""
    verify_tls: bool = True

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.token)

    @property
    def rest_url(self) -> str:
        return self.url.rstrip("/") + "/api"

    @property
    def ws_url(self) -> str:
        base = self.url.rstrip("/")
        if base.startswith("https://"):
            return "wss://" + base[len("https://") :] + "/api/websocket"
        if base.startswith("http://"):
            return "ws://" + base[len("http://") :] + "/api/websocket"
        raise ConfigError(f"home assistant url must be http(s): {self.url!r}")


@dataclass(slots=True)
class ESPHomeConfig:
    url: str = ""
    token: str = ""
    """Optional bearer/ingress token when the dashboard sits behind auth."""
    username: str = ""
    password: str = ""
    verify_tls: bool = True
    config_dir: str = "/config/esphome"
    ssh_host: str = ""
    """Optional SSH host used as a fallback for reading/writing YAML."""
    docker_container: str = ""
    """When set, the esphome CLI and YAML files live inside this Docker
    container on ``ssh_host`` (e.g. the Home Assistant ESPHome add-on), so
    commands run as ``docker exec <container> ...`` and ``config_dir`` is the
    path *inside* the container."""

    @property
    def enabled(self) -> bool:
        return bool(self.url or self.ssh_host)


@dataclass(slots=True)
class SSHHost:
    name: str
    host: str
    user: str = "root"
    port: int = 22
    key_file: str = ""
    password: str = ""
    known_hosts: str = ""
    """Path to a known_hosts file; empty disables host key checking."""
    description: str = ""


@dataclass(slots=True)
class SSHConfig:
    default_user: str = "root"
    default_key_file: str = ""
    default_known_hosts: str = ""
    connect_timeout: int = 15
    command_timeout: int = 300
    hosts: dict[str, SSHHost] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.hosts)


@dataclass(slots=True)
class FullyKioskDevice:
    name: str
    host: str
    password: str = ""
    port: int = 2323
    description: str = ""


@dataclass(slots=True)
class FullyKioskConfig:
    devices: dict[str, FullyKioskDevice] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.devices)


@dataclass(slots=True)
class NetworkConfig:
    subnets: list[str] = field(default_factory=list)
    default_ports: list[int] = field(
        default_factory=lambda: [22, 53, 80, 443, 445, 1883, 3000, 5432, 6052, 8006, 8123, 9000]
    )
    scan_concurrency: int = 256
    scan_timeout: float = 0.6


@dataclass(slots=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8787
    path: str = "/mcp"
    auth_token: str = ""
    """Bearer token required on every request.  Empty disables auth."""
    allowed_ips: list[str] = field(default_factory=list)
    """Optional CIDR/IP allowlist checked against the client / X-Forwarded-For."""
    trusted_proxies: list[str] = field(default_factory=list)
    audit_log: str = ""
    log_level: str = "INFO"
    max_output_bytes: int = 200_000
    guard_destructive: bool = False
    """When true, a best-effort filter refuses a few obviously catastrophic
    commands. This is a convenience backstop, NOT a security boundary — treat
    every configured client as holding full shell access."""
    insecure: bool = False
    """Explicit opt-in to serve with neither auth_token nor allowed_ips on a
    non-loopback bind. Without it the server refuses to start in that state."""


@dataclass(slots=True)
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    proxmox: ProxmoxConfig = field(default_factory=ProxmoxConfig)
    homeassistant: HomeAssistantConfig = field(default_factory=HomeAssistantConfig)
    esphome: ESPHomeConfig = field(default_factory=ESPHomeConfig)
    ssh: SSHConfig = field(default_factory=SSHConfig)
    fullykiosk: FullyKioskConfig = field(default_factory=FullyKioskConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    source: str = "<defaults>"

    @classmethod
    def from_dict(cls, raw: dict[str, Any], source: str = "<dict>") -> Config:
        data = expand(raw or {})
        cfg = cls(source=source)

        srv = data.get("server") or {}
        auth_token = srv.get("auth_token", os.environ.get("MCP_AUTH_TOKEN", ""))
        if not isinstance(auth_token, str):
            # A bare YAML scalar like `auth_token: no` / `off` / `0` parses to a
            # bool/int and would silently make `if token:` falsy, disabling auth.
            raise ConfigError(
                "server.auth_token must be a quoted string; got "
                f"{type(auth_token).__name__} {auth_token!r} (quote it, e.g. \"no\")"
            )
        cfg.server = ServerConfig(
            host=srv.get("host", os.environ.get("MCP_HOST", "0.0.0.0")),
            port=int(srv.get("port", os.environ.get("MCP_PORT", 8787))),
            path=srv.get("path", "/mcp"),
            auth_token=auth_token,
            allowed_ips=list(srv.get("allowed_ips") or []),
            trusted_proxies=list(srv.get("trusted_proxies") or []),
            audit_log=srv.get("audit_log", os.environ.get("MCP_AUDIT_LOG", "")),
            log_level=srv.get("log_level", os.environ.get("MCP_LOG_LEVEL", "INFO")),
            max_output_bytes=int(srv.get("max_output_bytes", 200_000)),
            guard_destructive=as_bool(srv.get("guard_destructive"), False),
            insecure=as_bool(srv.get("insecure"), False),
        )

        pve = data.get("proxmox") or {}
        cfg.proxmox = ProxmoxConfig(
            host=pve.get("host", ""),
            port=int(pve.get("port", 8006)),
            token_id=pve.get("token_id", ""),
            token_secret=pve.get("token_secret", ""),
            verify_tls=as_bool(pve.get("verify_tls"), False),
            node=pve.get("node", ""),
            _ssh_host=pve.get("ssh_host", ""),
        )

        ha = data.get("homeassistant") or {}
        cfg.homeassistant = HomeAssistantConfig(
            url=ha.get("url", ""),
            token=ha.get("token", ""),
            verify_tls=as_bool(ha.get("verify_tls"), True),
        )

        esp = data.get("esphome") or {}
        cfg.esphome = ESPHomeConfig(
            url=esp.get("url", ""),
            token=esp.get("token", ""),
            username=esp.get("username", ""),
            password=esp.get("password", ""),
            verify_tls=as_bool(esp.get("verify_tls"), True),
            config_dir=esp.get("config_dir", "/config/esphome"),
            ssh_host=esp.get("ssh_host", ""),
            docker_container=esp.get("docker_container", ""),
        )

        ssh = data.get("ssh") or {}
        hosts: dict[str, SSHHost] = {}
        default_user = ssh.get("default_user", "root")
        default_key = ssh.get("key_file", "")
        default_known = ssh.get("known_hosts", "")
        for name, spec in (ssh.get("hosts") or {}).items():
            spec = spec or {}
            if isinstance(spec, str):
                spec = {"host": spec}
            hosts[name] = SSHHost(
                name=name,
                host=spec.get("host", name),
                user=spec.get("user", default_user),
                port=int(spec.get("port", 22)),
                key_file=spec.get("key_file", default_key),
                password=spec.get("password", ""),
                known_hosts=spec.get("known_hosts", default_known),
                description=spec.get("description", ""),
            )
        cfg.ssh = SSHConfig(
            default_user=default_user,
            default_key_file=default_key,
            default_known_hosts=default_known,
            connect_timeout=int(ssh.get("connect_timeout", 15)),
            command_timeout=int(ssh.get("command_timeout", 300)),
            hosts=hosts,
        )

        fk = data.get("fullykiosk") or {}
        fk_default_password = fk.get("password", "")
        fk_default_port = int(fk.get("port", 2323))
        fk_devices: dict[str, FullyKioskDevice] = {}
        for name, spec in (fk.get("devices") or {}).items():
            spec = spec or {}
            if isinstance(spec, str):
                spec = {"host": spec}
            fk_devices[name] = FullyKioskDevice(
                name=name,
                host=spec.get("host", name),
                password=spec.get("password", fk_default_password),
                port=int(spec.get("port", fk_default_port)),
                description=spec.get("description", ""),
            )
        cfg.fullykiosk = FullyKioskConfig(devices=fk_devices)

        net = data.get("network") or {}
        cfg.network = NetworkConfig(
            subnets=list(net.get("subnets") or []),
            default_ports=list(net.get("ports") or NetworkConfig().default_ports),
            scan_concurrency=int(net.get("scan_concurrency", 256)),
            scan_timeout=float(net.get("scan_timeout", 0.6)),
        )
        return cfg

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> Config:
        candidates: list[Path] = []
        if path:
            candidates.append(Path(path))
        elif env_path := os.environ.get("HOMELAB_MCP_CONFIG"):
            candidates.append(Path(env_path))
        else:
            candidates.extend(DEFAULT_CONFIG_PATHS)

        for candidate in candidates:
            if candidate.is_file():
                raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
                if not isinstance(raw, dict):
                    raise ConfigError(f"{candidate}: top level must be a mapping")
                return cls.from_dict(raw, source=str(candidate))

        if path or os.environ.get("HOMELAB_MCP_CONFIG"):
            raise ConfigError(f"config file not found: {candidates[0]}")
        # No file at all: fall back to a pure-environment configuration so the
        # container can be driven by docker env vars alone.
        return cls.from_dict(_config_from_env(), source="<environment>")


def _config_from_env() -> dict[str, Any]:
    return {
        "server": {},
        "proxmox": {
            "host": os.environ.get("PROXMOX_HOST", ""),
            "port": int(os.environ.get("PROXMOX_PORT", 8006)),
            "token_id": os.environ.get("PROXMOX_TOKEN_ID", ""),
            "token_secret": os.environ.get("PROXMOX_TOKEN_SECRET", ""),
            "verify_tls": os.environ.get("PROXMOX_VERIFY_TLS", "false"),
            "ssh_host": os.environ.get("PROXMOX_SSH_HOST", ""),
        },
        "homeassistant": {
            "url": os.environ.get("HA_URL", ""),
            "token": os.environ.get("HA_TOKEN", ""),
        },
        "esphome": {
            "url": os.environ.get("ESPHOME_URL", ""),
            "token": os.environ.get("ESPHOME_TOKEN", ""),
        },
        "network": {
            "subnets": [s for s in os.environ.get("NET_SUBNETS", "").split(",") if s],
        },
    }
