"""Command line entry point."""

from __future__ import annotations

import argparse
import sys

from .config import Config, ConfigError
from .server import run


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
    args = parser.parse_args(argv)

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
