"""Network discovery: ping sweep, TCP port scan and ARP/neighbour tables."""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..context import Homelab
from ..errors import ToolError
from ..util import truncate

COMMON_PORT_NAMES = {
    22: "ssh",
    53: "dns",
    80: "http",
    443: "https",
    445: "smb",
    1883: "mqtt",
    3000: "grafana/app",
    5432: "postgres",
    6052: "esphome",
    8006: "proxmox",
    8123: "home-assistant",
    9000: "portainer/minio",
}


async def _tcp_open(host: str, port: int, timeout: float) -> bool:
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except (TimeoutError, OSError):
        return False


def register(mcp: FastMCP, lab: Homelab) -> None:
    net = lab.config.network
    limit = lab.config.server.max_output_bytes

    @mcp.tool()
    async def net_scan(
        subnet: str | None = None, ports: list[int] | None = None
    ) -> dict[str, Any]:
        """Scan a subnet for hosts with open TCP ports (service discovery).

        Runs from wherever this MCP server is hosted, so it sees the network the
        server sits on. For scanning a segment the server cannot reach, run the
        scan on a host via ssh_exec / proxmox_guest_exec instead.

        Args:
            subnet: CIDR like "192.168.1.0/24". Defaults to the first configured
                subnet. /16 or larger is refused to avoid runaway scans.
            ports: TCP ports to probe. Defaults to the configured common ports.
        """
        target = subnet or (net.subnets[0] if net.subnets else None)
        if not target:
            raise ToolError(
                "no subnet given and none configured; pass subnet=\"192.168.1.0/24\""
            )
        try:
            network = ipaddress.ip_network(target, strict=False)
        except ValueError as exc:
            raise ToolError(f"invalid subnet {target!r}: {exc}") from exc
        if network.num_addresses > 1024:
            raise ToolError(
                f"{target} has {network.num_addresses} addresses; scan a /22 or smaller"
            )

        probe_ports = ports or net.default_ports
        hosts = list(network.hosts()) if network.num_addresses > 2 else list(network)
        semaphore = asyncio.Semaphore(net.scan_concurrency)

        async def scan_host(ip: str) -> dict[str, Any] | None:
            open_ports: list[dict[str, Any]] = []
            for port in probe_ports:
                async with semaphore:
                    if await _tcp_open(ip, port, net.scan_timeout):
                        open_ports.append(
                            {"port": port, "service": COMMON_PORT_NAMES.get(port, "")}
                        )
            if open_ports:
                return {"ip": ip, "open_ports": open_ports}
            return None

        results = await asyncio.gather(*(scan_host(str(ip)) for ip in hosts))
        found = [r for r in results if r]
        found.sort(key=lambda r: ipaddress.ip_address(r["ip"]))
        return {
            "subnet": str(network),
            "ports": probe_ports,
            "hosts_up": len(found),
            "hosts": found,
        }

    @mcp.tool()
    async def net_ports(host: str, ports: list[int] | None = None) -> dict[str, Any]:
        """Check which TCP ports are open on a single host."""
        probe_ports = ports or net.default_ports
        checks = await asyncio.gather(
            *(_tcp_open(host, p, max(net.scan_timeout, 1.0)) for p in probe_ports)
        )
        return {
            "host": host,
            "open": [
                {"port": p, "service": COMMON_PORT_NAMES.get(p, "")}
                for p, ok in zip(probe_ports, checks, strict=False)
                if ok
            ],
        }

    @mcp.tool()
    async def net_neighbours(via_host: str | None = None) -> str:
        """Show the ARP / neighbour table (which MACs/IPs are seen on the LAN).

        Args:
            via_host: run on this ssh host; omit to read the table of the machine
                hosting this MCP server.
        """
        command = "ip neigh show 2>/dev/null || arp -a"
        if via_host:
            result = await lab.ssh.run(via_host, command)
            return truncate(result.stdout + result.stderr, limit)
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        return truncate(out.decode("utf-8", "replace"), limit)

    @mcp.tool()
    async def net_ping(host: str, count: int = 3, via_host: str | None = None) -> str:
        """Ping a host to check reachability and latency."""
        command = f"ping -c {int(count)} -W 2 {host}"
        if via_host:
            result = await lab.ssh.run(via_host, command, timeout=count * 3 + 5)
            return truncate(result.stdout + result.stderr, limit)
        proc = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await proc.communicate()
        return truncate(out.decode("utf-8", "replace"), limit)
