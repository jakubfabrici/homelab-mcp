"""ESPHome tools: list nodes, read/edit YAML, validate and flash over OTA.

Three transports, chosen by configuration:

* ``esphome.url`` set — talk to the ESPHome **dashboard** add-on over
  HTTP/websocket.
* ``esphome.ssh_host`` set, no ``docker_container`` — the ``esphome`` CLI and
  the YAML files live directly on that host.
* ``esphome.ssh_host`` + ``docker_container`` set — the CLI and YAML live
  **inside a Docker container** on that host (the typical Home Assistant OS +
  ESPHome add-on layout, where the add-on runs as e.g.
  ``app_5c53de3b_esphome`` and there is no ``esphome`` binary on the HAOS host
  itself). Commands run as ``docker exec <container> ...`` and ``config_dir``
  is the path inside the container.
"""

from __future__ import annotations

import shlex
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..context import Homelab
from ..errors import NotConfigured
from ..util import truncate


def register(mcp: FastMCP, lab: Homelab) -> None:
    esp = lab.esphome
    cfg = lab.config.esphome
    limit = lab.config.server.max_output_bytes

    def _use_ssh() -> bool:
        return not cfg.url and bool(cfg.ssh_host)

    def _in_container() -> bool:
        return bool(cfg.docker_container)

    def _norm(name: str) -> str:
        return name if name.endswith(".yaml") else f"{name}.yaml"

    def _yaml_path(name: str) -> str:
        return f"{cfg.config_dir.rstrip('/')}/{_norm(name)}"

    def _exec_prefix() -> str:
        if _in_container():
            return f"docker exec {shlex.quote(cfg.docker_container)} "
        return ""

    async def _cli(args: str, timeout: int) -> Any:
        """Run an ``esphome <args>`` command on the ssh host (optionally in-container)."""
        return await lab.ssh.run(cfg.ssh_host, f"{_exec_prefix()}esphome {args}", timeout=timeout)

    async def _read_yaml(name: str) -> str:
        path = _yaml_path(name)
        if _in_container():
            result = await lab.ssh.run(
                cfg.ssh_host,
                f"docker exec {shlex.quote(cfg.docker_container)} cat {shlex.quote(path)}",
                check=True,
            )
            return result.stdout
        return await lab.ssh.read_file(cfg.ssh_host, path)

    async def _backup_yaml(name: str) -> str | None:
        path = _yaml_path(name)
        quoted = shlex.quote(path)
        stamp = "$(date +%Y%m%d%H%M%S)"
        inner = (
            f'if [ -f {quoted} ]; then b={quoted}.bak-{stamp}; '
            f'cp -p {quoted} "$b"; echo "$b"; fi'
        )
        if _in_container():
            cmd = f"docker exec {shlex.quote(cfg.docker_container)} sh -c {shlex.quote(inner)}"
        else:
            cmd = inner
        probe = await lab.ssh.run(cfg.ssh_host, cmd)
        return probe.stdout.strip() or None

    async def _write_yaml(name: str, content: str) -> None:
        path = _yaml_path(name)
        if _in_container():
            cmd = (
                f"docker exec -i {shlex.quote(cfg.docker_container)} "
                f"sh -c {shlex.quote(f'cat > {shlex.quote(path)}')}"
            )
            await lab.ssh.run(cfg.ssh_host, cmd, stdin=content, check=True)
        else:
            await lab.ssh.write_file(cfg.ssh_host, path, content)

    @mcp.tool()
    async def esphome_devices() -> Any:
        """List ESPHome nodes known to the dashboard (or YAML files over SSH)."""
        if _use_ssh():
            listing = f"ls -1 {shlex.quote(cfg.config_dir)}/*.yaml 2>/dev/null"
            cmd = f"{_exec_prefix()}sh -c {shlex.quote(listing)}" if _in_container() else listing
            result = await lab.ssh.run(cfg.ssh_host, cmd)
            files = [line.rsplit("/", 1)[-1] for line in result.stdout.split() if line.strip()]
            source = f"ssh:{cfg.ssh_host}"
            if _in_container():
                source += f" (docker:{cfg.docker_container})"
            return {"source": source, "configs": files}
        data = await esp.devices()
        configured = [
            {
                "name": d.get("name"),
                "configuration": d.get("configuration"),
                "address": d.get("address"),
                "online": (
                    d.get("loaded_integrations") is not None or d.get("online")
                ),
                "target_platform": d.get("target_platform"),
                "current_version": d.get("current_version"),
                "deployed_version": d.get("deployed_version"),
            }
            for d in (data.get("configured") or [])
        ]
        return {
            "source": "dashboard",
            "configured": configured,
            "importable": data.get("importable", []),
        }

    @mcp.tool()
    async def esphome_read(configuration: str) -> str:
        """Read an ESPHome YAML configuration.

        Args:
            configuration: the file name, e.g. "studna.yaml" or "kury".
        """
        if _use_ssh():
            return truncate(await _read_yaml(configuration), limit)
        return truncate(await esp.read_config(_norm(configuration)), limit)

    @mcp.tool()
    async def esphome_write(
        configuration: str, content: str, backup: bool = True
    ) -> dict[str, Any]:
        """Write an ESPHome YAML configuration (validate afterwards before flashing).

        Args:
            configuration: the file name, e.g. "zvoncek.yaml".
            content: the full new YAML contents.
            backup: over SSH, keep a timestamped backup of the previous file.
        """
        name = _norm(configuration)
        lab.audit.record("esphome_write", configuration=name, bytes=len(content))
        if _use_ssh():
            backup_path = await _backup_yaml(name) if backup else None
            await _write_yaml(name, content)
            return {"configuration": name, "path": _yaml_path(name), "backup": backup_path}
        await esp.write_config(name, content)
        return {"configuration": name, "written": True}

    @mcp.tool()
    async def esphome_validate(configuration: str) -> dict[str, Any]:
        """Validate an ESPHome configuration without flashing."""
        name = _norm(configuration)
        lab.audit.record("esphome_validate", configuration=name)
        if _use_ssh():
            result = await _cli(f"config {shlex.quote(_yaml_path(name))}", timeout=300)
            return {
                "configuration": name,
                "success": result.exit_status == 0,
                "output": truncate(result.stdout + result.stderr, limit),
            }
        return await esp.run_action("validate", name, timeout=300)

    @mcp.tool()
    async def esphome_flash(configuration: str, method: str = "OTA") -> dict[str, Any]:
        """Compile and upload an ESPHome configuration to the device.

        Args:
            configuration: the file name, e.g. "kury.yaml".
            method: "OTA" (default, over the network) or "compile" to only build.
        """
        name = _norm(configuration)
        lab.audit.record("esphome_flash", configuration=name, method=method)
        if _use_ssh():
            verb = "compile" if method.lower() == "compile" else "run --no-logs"
            result = await _cli(f"{verb} {shlex.quote(_yaml_path(name))}", timeout=1200)
            return {
                "configuration": name,
                "method": method,
                "success": result.exit_status == 0,
                "output": truncate(result.stdout + result.stderr, limit),
            }
        action = "compile" if method.lower() == "compile" else "upload"
        return await esp.run_action(action, name, port="OTA", timeout=1200)

    @mcp.tool()
    async def esphome_delete(configuration: str) -> dict[str, Any]:
        """Delete an ESPHome configuration file (a backup is kept over SSH)."""
        name = _norm(configuration)
        lab.audit.record("esphome_delete", configuration=name)
        if _use_ssh():
            path = _yaml_path(name)
            quoted = shlex.quote(path)
            inner = (
                f"if [ -f {quoted} ]; then mv {quoted} {quoted}.deleted-$(date +%Y%m%d%H%M%S); "
                f"echo removed; else echo 'not found'; fi"
            )
            if _in_container():
                cmd = f"docker exec {shlex.quote(cfg.docker_container)} sh -c {shlex.quote(inner)}"
            else:
                cmd = inner
            result = await lab.ssh.run(cfg.ssh_host, cmd)
            return {"configuration": name, "result": result.stdout.strip()}
        await esp.delete_config(name)
        return {"configuration": name, "deleted": True}

    @mcp.tool()
    async def esphome_logs(configuration: str, lines: int = 200) -> str:
        """Stream device logs for a short window (OTA logs; may time out if noisy)."""
        name = _norm(configuration)
        if _use_ssh():
            result = await _cli(
                f"logs {shlex.quote(_yaml_path(name))} 2>&1 | head -n {int(lines)}",
                timeout=40,
            )
            return truncate(result.stdout + result.stderr, limit)
        if not cfg.url:
            raise NotConfigured("ESPHome dashboard")
        result = await esp.run_action("logs", name, timeout=30, max_lines=lines)
        return truncate(result.get("output", ""), limit)
