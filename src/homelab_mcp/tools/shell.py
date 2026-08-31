"""Shell access to homelab hosts over SSH, plus Docker management on top of it."""

from __future__ import annotations

import json
import shlex
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..context import Homelab
from ..errors import ToolError
from ..util import truncate

DOCKER_ACTIONS = {"start", "stop", "restart", "pause", "unpause", "rm", "kill"}


def register(mcp: FastMCP, lab: Homelab) -> None:
    limit = lab.config.server.max_output_bytes

    @mcp.tool()
    async def ssh_hosts() -> list[dict[str, Any]]:
        """List the hosts this server can reach over SSH."""
        return [
            {
                "name": host.name,
                "address": host.host,
                "user": host.user,
                "port": host.port,
                "description": host.description,
            }
            for host in lab.ssh.hosts().values()
        ]

    @mcp.tool()
    async def ssh_exec(
        host: str, command: str, timeout: int = 300, stdin: str | None = None
    ) -> dict[str, Any]:
        """Run a shell command on a homelab host over SSH.

        Args:
            host: a name from ssh_hosts, or a bare hostname/IP on the LAN.
            command: the command line to run (executed by the login shell).
            timeout: seconds before the command is abandoned.
            stdin: optional text piped into the command's standard input.
        """
        lab.audit.record("ssh_exec", host=host, command=command)
        result = await lab.ssh.run(host, command, timeout=timeout, stdin=stdin)
        return {
            "host": result.host,
            "exit_status": result.exit_status,
            "stdout": truncate(result.stdout, limit),
            "stderr": truncate(result.stderr, limit),
        }

    @mcp.tool()
    async def ssh_read_file(host: str, path: str) -> str:
        """Read a text file from a host over SSH."""
        lab.audit.record("ssh_read_file", host=host, path=path)
        return truncate(await lab.ssh.read_file(host, path), limit)

    @mcp.tool()
    async def ssh_write_file(
        host: str, path: str, content: str, backup: bool = True
    ) -> dict[str, Any]:
        """Write a text file on a host over SSH, keeping a timestamped backup.

        Args:
            host: target host.
            path: absolute path of the file to write.
            content: the full new contents of the file.
            backup: copy the existing file to ``<path>.bak-<timestamp>`` first.
        """
        lab.audit.record("ssh_write_file", host=host, path=path, bytes=len(content))
        backup_path = None
        if backup:
            quoted = shlex.quote(path)
            probe = await lab.ssh.run(
                host,
                f"if [ -f {quoted} ]; then "
                f"b={quoted}.bak-$(date +%Y%m%d%H%M%S); cp -p {quoted} \"$b\"; echo \"$b\"; fi",
            )
            backup_path = probe.stdout.strip() or None
        await lab.ssh.write_file(host, path, content)
        return {"host": host, "path": path, "bytes": len(content), "backup": backup_path}

    @mcp.tool()
    async def ssh_service(host: str, unit: str, action: str = "status") -> dict[str, Any]:
        """Control or inspect a systemd unit on a host.

        Args:
            host: target host.
            unit: systemd unit name, e.g. "nginx" or "docker.service".
            action: status, start, stop, restart, reload, enable, disable, is-active.
        """
        allowed = {
            "status",
            "start",
            "stop",
            "restart",
            "reload",
            "enable",
            "disable",
            "is-active",
            "is-enabled",
        }
        if action not in allowed:
            raise ToolError(f"unknown systemd action {action!r}; use one of {sorted(allowed)}")
        lab.audit.record("ssh_service", host=host, unit=unit, action=action)
        flags = "--no-pager -l" if action == "status" else ""
        result = await lab.ssh.run(host, f"systemctl {flags} {action} {shlex.quote(unit)}")
        return {
            "host": host,
            "unit": unit,
            "action": action,
            "exit_status": result.exit_status,
            "output": truncate(result.stdout + result.stderr, limit),
        }

    @mcp.tool()
    async def ssh_journal(
        host: str, unit: str | None = None, lines: int = 100, since: str | None = None
    ) -> str:
        """Read systemd journal entries from a host.

        Args:
            host: target host.
            unit: restrict to one unit; omit for the whole journal.
            lines: number of trailing lines.
            since: journalctl time expression, e.g. "1 hour ago".
        """
        parts = ["journalctl", "--no-pager", "-n", str(int(lines))]
        if unit:
            parts += ["-u", shlex.quote(unit)]
        if since:
            parts += ["--since", shlex.quote(since)]
        result = await lab.ssh.run(host, " ".join(parts))
        return truncate(result.stdout + result.stderr, limit)

    @mcp.tool()
    async def host_overview(host: str) -> dict[str, Any]:
        """One-shot health snapshot of a host: uptime, load, disk, memory, top processes."""
        script = (
            "echo '### uptime'; uptime; "
            "echo '### os'; (cat /etc/os-release 2>/dev/null | head -2); "
            "echo '### memory'; free -h; "
            "echo '### disk'; df -h -x tmpfs -x devtmpfs; "
            "echo '### top'; ps -eo pcpu,pmem,comm --sort=-pcpu | head -11; "
            "echo '### listening'; (ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null) | head -30"
        )
        result = await lab.ssh.run(host, script)
        return {"host": host, "report": truncate(result.stdout + result.stderr, limit)}

    # -- docker ---------------------------------------------------------------

    @mcp.tool()
    async def docker_ps(host: str, all_containers: bool = True) -> list[dict[str, Any]]:
        """List Docker containers on a host.

        Args:
            host: target host running the Docker daemon.
            all_containers: include stopped containers (default true).
        """
        flag = "-a" if all_containers else ""
        fmt = "{{json .}}"
        result = await lab.ssh.run(host, f"docker ps {flag} --format {shlex.quote(fmt)}")
        if result.exit_status != 0:
            raise ToolError(f"docker ps on {host} failed: {(result.stderr or result.stdout)[:400]}")
        containers = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                containers.append(json.loads(line))
            except ValueError:
                containers.append({"raw": line})
        return containers

    @mcp.tool()
    async def docker_logs(
        host: str, container: str, lines: int = 200, since: str | None = None
    ) -> str:
        """Tail a container's logs."""
        parts = ["docker", "logs", "--tail", str(int(lines))]
        if since:
            parts += ["--since", shlex.quote(since)]
        parts.append(shlex.quote(container))
        result = await lab.ssh.run(host, " ".join(parts))
        return truncate(result.stdout + result.stderr, limit)

    @mcp.tool()
    async def docker_action(host: str, container: str, action: str) -> dict[str, Any]:
        """Start, stop, restart, pause, unpause, kill or remove a container."""
        if action not in DOCKER_ACTIONS:
            raise ToolError(
                f"unknown docker action {action!r}; use one of {sorted(DOCKER_ACTIONS)}"
            )
        lab.audit.record("docker_action", host=host, container=container, action=action)
        result = await lab.ssh.run(host, f"docker {action} {shlex.quote(container)}")
        return {
            "host": host,
            "container": container,
            "action": action,
            "exit_status": result.exit_status,
            "output": truncate(result.stdout + result.stderr, limit),
        }

    @mcp.tool()
    async def docker_exec(
        host: str, container: str, command: str, timeout: int = 120
    ) -> dict[str, Any]:
        """Run a command inside a Docker container."""
        lab.audit.record("docker_exec", host=host, container=container, command=command)
        wrapped = (
            f"docker exec {shlex.quote(container)} sh -lc {shlex.quote(command)}"
        )
        result = await lab.ssh.run(host, wrapped, timeout=timeout)
        return {
            "host": host,
            "container": container,
            "exit_status": result.exit_status,
            "stdout": truncate(result.stdout, limit),
            "stderr": truncate(result.stderr, limit),
        }

    @mcp.tool()
    async def docker_compose(
        host: str, project_dir: str, command: str = "ps", timeout: int = 600
    ) -> dict[str, Any]:
        """Run a docker compose command in a project directory.

        Args:
            host: target host.
            project_dir: directory containing docker-compose.yml.
            command: compose subcommand and flags, e.g. "up -d", "pull", "logs --tail 50".
            timeout: seconds before the command is abandoned.
        """
        lab.audit.record("docker_compose", host=host, project_dir=project_dir, command=command)
        wrapped = f"cd {shlex.quote(project_dir)} && docker compose {command}"
        result = await lab.ssh.run(host, wrapped, timeout=timeout)
        return {
            "host": host,
            "project_dir": project_dir,
            "command": command,
            "exit_status": result.exit_status,
            "output": truncate(result.stdout + result.stderr, limit),
        }
