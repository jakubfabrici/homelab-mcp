"""A single orientation tool that maps the whole homelab in one call."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..context import Homelab
from ..errors import ToolError


def register(mcp: FastMCP, lab: Homelab) -> None:
    @mcp.tool()
    async def homelab_overview() -> dict[str, Any]:
        """Map the whole homelab in one call: which backends are configured and,
        for each reachable one, a short live inventory (Proxmox nodes/guests,
        Home Assistant entity counts, ESPHome nodes, SSH hosts).

        Call this first when you are unsure what is available.
        """
        modules = lab.enabled_modules()
        report: dict[str, Any] = {"modules": modules, "config_source": lab.config.source}

        if modules["proxmox"]:
            try:
                nodes = await lab.proxmox.nodes()
                guests = await lab.proxmox.guests()
                running = sum(1 for g in guests if g.get("status") == "running")
                report["proxmox"] = {
                    "nodes": [n.get("node") for n in nodes],
                    "guests_total": len(guests),
                    "guests_running": running,
                    "vms": sum(1 for g in guests if g.get("type") == "qemu"),
                    "containers": sum(1 for g in guests if g.get("type") == "lxc"),
                }
            except ToolError as exc:
                report["proxmox"] = {"error": str(exc)}

        if modules["homeassistant"]:
            try:
                states = await lab.ha.request("GET", "/states") or []
                domains: dict[str, int] = {}
                for state in states:
                    domain = str(state.get("entity_id", "")).split(".", 1)[0]
                    domains[domain] = domains.get(domain, 0) + 1
                report["homeassistant"] = {
                    "entities": len(states),
                    "top_domains": dict(
                        sorted(domains.items(), key=lambda kv: kv[1], reverse=True)[:12]
                    ),
                }
            except ToolError as exc:
                report["homeassistant"] = {"error": str(exc)}

        if modules["esphome"]:
            try:
                if lab.config.esphome.url:
                    data = await lab.esphome.devices()
                    report["esphome"] = {
                        "nodes": [d.get("name") for d in (data.get("configured") or [])]
                    }
                else:
                    report["esphome"] = {"source": f"ssh:{lab.config.esphome.ssh_host}"}
            except ToolError as exc:
                report["esphome"] = {"error": str(exc)}

        if modules["ssh"]:
            report["ssh_hosts"] = [
                {"name": h.name, "address": h.host, "description": h.description}
                for h in lab.ssh.hosts().values()
            ]
        report["network_subnets"] = lab.config.network.subnets
        return report
