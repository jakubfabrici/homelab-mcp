"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import Config, ConfigError
from .server import run

DEFAULT_ENV_FILE = "/etc/homelab-mcp/homelab-mcp.env"


def load_env_file(path: str | os.PathLike[str]) -> int:
    """Load ``KEY=VALUE`` lines into the environment without shell evaluation.

    systemd's ``EnvironmentFile`` populates the running service, but a bare
    ``homelab-mcp --check`` from a shell does not see those variables. Parsing
    the file here (values taken literally — no ``$`` expansion, no redirection)
    makes the CLI behave the same as the service, and avoids the trap where a
    value containing a shell metacharacter breaks ``set -a; . env``.
    Existing environment variables win, so real exports are never overridden.
    """
    file = Path(path)
    if not file.is_file():
        return 0
    loaded = 0
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="homelab-mcp",
        description="MCP server for a Proxmox / Home Assistant / ESPHome home lab.",
    )
    parser.add_argument(
        "-c",
        "--config",
        help="path to homelab.yaml (default: $HOMELAB_MCP_CONFIG or config/homelab.yaml)",
    )
    parser.add_argument(
        "--check", action="store_true", help="load config, print a summary and exit"
    )
    parser.add_argument(
        "--env-file",
        default=os.environ.get("HOMELAB_MCP_ENV", DEFAULT_ENV_FILE),
        help=f"KEY=VALUE secrets file to load (default: {DEFAULT_ENV_FILE}); ignored if absent",
    )
    args = parser.parse_args(argv)

    load_env_file(args.env_file)

    try:
        config = Config.load(args.config)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        print(f"config source: {config.source}")
        print(f"server:        {config.server.host}:{config.server.port}{config.server.path}")
        print(f"auth token:    {'set' if config.server.auth_token else 'MISSING (open!)'}")
        print(f"ip allowlist:  {config.server.allowed_ips or 'none'}")
        print("modules:")
        print(f"  proxmox:       {'yes' if config.proxmox.enabled else 'no'}")
        print(f"  homeassistant: {'yes' if config.homeassistant.enabled else 'no'}")
        print(f"  esphome:       {'yes' if config.esphome.enabled else 'no'}")
        print(f"  ssh hosts:     {', '.join(config.ssh.hosts) or 'none'}")
        print(f"  net subnets:   {', '.join(config.network.subnets) or 'none'}")
        return 0

    run(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
