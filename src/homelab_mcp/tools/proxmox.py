"""Proxmox VE tools: inventory, lifecycle, snapshots and in-guest execution."""

from __future__ import annotations

import asyncio
import shlex
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..context import Homelab
from ..errors import ToolError
from ..util import human_bytes, human_uptime, truncate

GUEST_ACTIONS = {
    "start",
    "stop",
    "shutdown",
    "reboot",
    "reset",
    "suspend",
    "resume",
}


def _summarise_guest(guest: dict[str, Any]) -> dict[str, Any]:
    return {
        "vmid": guest.get("vmid"),
        "name": guest.get("name"),
        "type": "VM" if guest.get("type") == "qemu" else "LXC",
        "node": guest.get("node"),
        "status": guest.get("status"),
        "cpus": guest.get("maxcpu"),
        "memory": human_bytes(guest.get("maxmem")),
        "disk": human_bytes(guest.get("maxdisk")),
        "uptime": human_uptime(guest.get("uptime")),
        "tags": guest.get("tags"),
        "template": bool(guest.get("template")),
    }


def register(mcp: FastMCP, lab: Homelab) -> None:
    pve = lab.proxmox

    @mcp.tool()
    async def proxmox_nodes() -> list[dict[str, Any]]:
        """List Proxmox cluster nodes with CPU, memory and uptime."""
        nodes = await pve.nodes()
        return [
            {
                "node": n.get("node"),
                "status": n.get("status"),
                "cpu_percent": round((n.get("cpu") or 0) * 100, 1),
                "cpu_count": n.get("maxcpu"),
                "memory_used": human_bytes(n.get("mem")),
                "memory_total": human_bytes(n.get("maxmem")),
                "uptime": human_uptime(n.get("uptime")),
            }
            for n in nodes
        ]

    @mcp.tool()
    async def proxmox_guests(
        status: str | None = None, kind: str | None = None, search: str | None = None
    ) -> list[dict[str, Any]]:
        """List every VM and LXC container in the cluster.

        The inventory is read live from the cluster on each call, so guests you
        create later show up without any change to this server.

        Args:
            status: optional filter, e.g. "running" or "stopped".
            kind: optional filter, "qemu"/"vm" or "lxc"/"ct".
            search: optional case-insensitive substring matched against the name.
        """
        guests = await pve.guests()
        if kind:
            wanted = "qemu" if kind.lower() in {"qemu", "vm", "kvm"} else "lxc"
            guests = [g for g in guests if g.get("type") == wanted]
        if status:
            guests = [g for g in guests if (g.get("status") or "").lower() == status.lower()]
        if search:
            needle = search.lower()
            guests = [g for g in guests if needle in str(g.get("name", "")).lower()]
        guests.sort(key=lambda g: int(g.get("vmid", 0)))
        return [_summarise_guest(g) for g in guests]

    @mcp.tool()
    async def proxmox_guest_status(vmid: int) -> dict[str, Any]:
        """Detailed runtime status plus configuration of one VM or container."""
        guest = await pve.find_guest(vmid)
        base = pve.guest_path(guest)
        current, config = await asyncio.gather(
            pve.request("GET", f"{base}/status/current"),
            pve.request("GET", f"{base}/config"),
        )
        return {
            "summary": _summarise_guest(guest),
            "status": current,
            "config": config,
        }

    @mcp.tool()
    async def proxmox_guest_action(vmid: int, action: str, wait: bool = True) -> dict[str, Any]:
        """Start, stop, shutdown, reboot, reset, suspend or resume a guest.

        Args:
            vmid: numeric guest id.
            action: one of start, stop, shutdown, reboot, reset, suspend, resume.
            wait: block until the Proxmox task finishes (default true).
        """
        action = action.lower().strip()
        if action not in GUEST_ACTIONS:
            raise ToolError(f"unknown action {action!r}; use one of {sorted(GUEST_ACTIONS)}")
        guest = await pve.find_guest(vmid)
        base = pve.guest_path(guest)
        lab.audit.record("proxmox_guest_action", vmid=vmid, action=action, node=guest["node"])
        upid = await pve.request("POST", f"{base}/status/{action}")
        result: dict[str, Any] = {"vmid": vmid, "action": action, "upid": upid}
        if wait and isinstance(upid, str):
            result["task"] = await pve.wait_for_task(guest["node"], upid)
        return result

    @mcp.tool()
    async def proxmox_guest_exec(
        vmid: int, command: str, timeout: int = 120
    ) -> dict[str, Any]:
        """Run a shell command *inside* a guest.

        LXC containers are driven with ``pct exec`` from their node over SSH;
        QEMU VMs go through the QEMU guest agent, which must be installed and
        running in the VM.

        Args:
            vmid: numeric guest id.
            command: shell command line, executed with ``sh -lc``.
            timeout: seconds to wait for completion.
        """
        guest = await pve.find_guest(vmid)
        lab.audit.record("proxmox_guest_exec", vmid=vmid, command=command, node=guest["node"])

        if guest.get("type") == "lxc":
            node_host = lab.config.proxmox.ssh_host
            wrapped = f"pct exec {int(vmid)} -- sh -lc {shlex.quote(command)}"
            result = await lab.ssh.run(node_host, wrapped, timeout=timeout)
            return {
                "vmid": vmid,
                "via": f"pct exec on {node_host}",
                "exit_status": result.exit_status,
                "stdout": truncate(result.stdout, lab.config.server.max_output_bytes),
                "stderr": truncate(result.stderr, lab.config.server.max_output_bytes),
            }

        base = pve.guest_path(guest)
        started = await pve.request(
            "POST", f"{base}/agent/exec", data={"command": ["sh", "-lc", command]}
        )
        pid = (started or {}).get("pid")
        if pid is None:
            raise ToolError(f"guest agent did not return a pid for vmid {vmid}: {started}")

        deadline = asyncio.get_running_loop().time() + timeout
        status: dict[str, Any] = {}
        while asyncio.get_running_loop().time() < deadline:
            status = await pve.request(
                "GET", f"{base}/agent/exec-status", params={"pid": pid}
            ) or {}
            if status.get("exited"):
                break
            await asyncio.sleep(0.5)
        else:
            raise ToolError(f"guest agent command on vmid {vmid} timed out after {timeout}s")

        limit = lab.config.server.max_output_bytes
        return {
            "vmid": vmid,
            "via": "qemu-guest-agent",
            "exit_status": status.get("exitcode"),
            "stdout": truncate(status.get("out-data") or "", limit),
            "stderr": truncate(status.get("err-data") or "", limit),
        }

    @mcp.tool()
    async def proxmox_guest_config_set(vmid: int, settings: dict[str, Any]) -> dict[str, Any]:
        """Change a guest's configuration (cores, memory, net0, onboot, ...).

        Args:
            vmid: numeric guest id.
            settings: Proxmox config keys, e.g. {"memory": 4096, "cores": 2}.
        """
        guest = await pve.find_guest(vmid)
        base = pve.guest_path(guest)
        lab.audit.record("proxmox_guest_config_set", vmid=vmid, settings=settings)
        await pve.request("PUT", f"{base}/config", data=settings)
        return {
            "vmid": vmid,
            "applied": settings,
            "config": await pve.request("GET", f"{base}/config"),
        }

    @mcp.tool()
    async def proxmox_snapshots(vmid: int) -> list[dict[str, Any]]:
        """List snapshots of a guest."""
        guest = await pve.find_guest(vmid)
        return await pve.request("GET", f"{pve.guest_path(guest)}/snapshot") or []

    @mcp.tool()
    async def proxmox_snapshot(
        vmid: int, name: str, operation: str = "create", description: str = "", wait: bool = True
    ) -> dict[str, Any]:
        """Create, delete or roll back to a snapshot.

        Args:
            vmid: numeric guest id.
            name: snapshot name.
            operation: "create", "delete" or "rollback".
            description: free text stored with a newly created snapshot.
            wait: block until the Proxmox task finishes.
        """
        operation = operation.lower().strip()
        guest = await pve.find_guest(vmid)
        base = f"{pve.guest_path(guest)}/snapshot"
        lab.audit.record("proxmox_snapshot", vmid=vmid, name=name, operation=operation)
        if operation == "create":
            upid = await pve.request(
                "POST", base, data={"snapname": name, "description": description}
            )
        elif operation == "delete":
            upid = await pve.request("DELETE", f"{base}/{name}")
        elif operation == "rollback":
            upid = await pve.request("POST", f"{base}/{name}/rollback")
        else:
            raise ToolError("operation must be create, delete or rollback")
        result: dict[str, Any] = {
            "vmid": vmid,
            "snapshot": name,
            "operation": operation,
            "upid": upid,
        }
        if wait and isinstance(upid, str):
            result["task"] = await pve.wait_for_task(guest["node"], upid)
        return result

    @mcp.tool()
    async def proxmox_storage() -> list[dict[str, Any]]:
        """Storage pools across the cluster with usage."""
        entries = await pve.cluster_resources("storage")
        return [
            {
                "storage": e.get("storage"),
                "node": e.get("node"),
                "type": e.get("plugintype") or e.get("type"),
                "status": e.get("status"),
                "used": human_bytes(e.get("disk")),
                "total": human_bytes(e.get("maxdisk")),
                "used_percent": round((e.get("disk") or 0) / (e.get("maxdisk") or 1) * 100, 1),
            }
            for e in entries
        ]

    @mcp.tool()
    async def proxmox_tasks(node: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Recent Proxmox tasks, newest first (useful after an action failed)."""
        nodes = [node] if node else [n["node"] for n in await pve.nodes()]
        tasks: list[dict[str, Any]] = []
        for name in nodes:
            entries = await pve.request(
                "GET", f"/nodes/{name}/tasks", params={"limit": limit}
            ) or []
            tasks.extend(entries)
        tasks.sort(key=lambda t: t.get("starttime", 0), reverse=True)
        return tasks[:limit]

    @mcp.tool()
    async def proxmox_api(
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Call any Proxmox VE API endpoint directly (escape hatch).

        Use this for anything the named tools do not cover — cluster firewall,
        backups, replication, users, new features. Paths are relative to
        ``/api2/json``, e.g. "/cluster/backup" or "/nodes/pve/disks/list".

        Args:
            method: GET, POST, PUT or DELETE.
            path: API path below /api2/json.
            params: query string parameters.
            data: form body for POST/PUT.
        """
        lab.audit.record("proxmox_api", method=method, path=path, params=params, data=data)
        return await pve.request(method, path, params=params, data=data)
