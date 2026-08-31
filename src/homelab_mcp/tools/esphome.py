"""ESPHome tools: list nodes, read/edit YAML, validate and flash over OTA.

Works either against the ESPHome dashboard add-on (HTTP/websocket) or, when
only ``esphome.ssh_host`` is configured, by editing the YAML files directly
over SSH and driving the ``esphome`` CLI on the host.
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

    def _yaml_path(name: str) -> str:
        name = name if name.endswith(".yaml") else f"{name}.yaml"
        return f"{cfg.config_dir.rstrip('/')}/{name}"

    @mcp.tool()
    async def esphome_devices() -> Any:
        """List ESPHome nodes known to the dashboard (or YAML files over SSH)."""
        if _use_ssh():
            result = await lab.ssh.run(
                cfg.ssh_host,
                f"ls -1 {shlex.quote(cfg.config_dir)}/*.yaml 2>/dev/null",
            )
            files = [line.rsplit("/", 1)[-1] for line in result.stdout.split() if line.strip()]
            return {"source": f"ssh:{cfg.ssh_host}", "configs": files}
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
            return truncate(await lab.ssh.read_file(cfg.ssh_host, _yaml_path(configuration)), limit)
        name = configuration if configuration.endswith(".yaml") else f"{configuration}.yaml"
        return truncate(await esp.read_config(name), limit)

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
        name = configuration if configuration.endswith(".yaml") else f"{configuration}.yaml"
        lab.audit.record("esphome_write", configuration=name, bytes=len(content))
        if _use_ssh():
            path = _yaml_path(name)
            backup_path = None
            if backup:
                quoted = shlex.quote(path)
                probe = await lab.ssh.run(
                    cfg.ssh_host,
                    f"if [ -f {quoted} ]; then b={quoted}.bak-$(date +%Y%m%d%H%M%S); "
                    f"cp -p {quoted} \"$b\"; echo \"$b\"; fi",
                )
                backup_path = probe.stdout.strip() or None
            await lab.ssh.write_file(cfg.ssh_host, path, content)
            return {"configuration": name, "path": path, "backup": backup_path}
        await esp.write_config(name, content)
        return {"configuration": name, "written": True}

    @mcp.tool()
    async def esphome_validate(configuration: str) -> dict[str, Any]:
        """Validate an ESPHome configuration without flashing."""
        name = configuration if configuration.endswith(".yaml") else f"{configuration}.yaml"
        lab.audit.record("esphome_validate", configuration=name)
        if _use_ssh():
            result = await lab.ssh.run(
                cfg.ssh_host,
                f"esphome config {shlex.quote(_yaml_path(name))}",
                timeout=300,
            )
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
        name = configuration if configuration.endswith(".yaml") else f"{configuration}.yaml"
        lab.audit.record("esphome_flash", configuration=name, method=method)
        if _use_ssh():
            verb = "compile" if method.lower() == "compile" else "run --no-logs"
            result = await lab.ssh.run(
                cfg.ssh_host,
                f"esphome {verb} {shlex.quote(_yaml_path(name))}",
                timeout=1200,
            )
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
        name = configuration if configuration.endswith(".yaml") else f"{configuration}.yaml"
        lab.audit.record("esphome_delete", configuration=name)
        if _use_ssh():
            path = _yaml_path(name)
            quoted = shlex.quote(path)
            result = await lab.ssh.run(
                cfg.ssh_host,
                f"if [ -f {quoted} ]; then mv {quoted} {quoted}.deleted-$(date +%Y%m%d%H%M%S); "
                f"echo removed; else echo 'not found'; fi",
            )
            return {"configuration": name, "result": result.stdout.strip()}
        await esp.delete_config(name)
        return {"configuration": name, "deleted": True}

    @mcp.tool()
    async def esphome_logs(configuration: str, lines: int = 200) -> str:
        """Stream device logs for a short window (OTA logs; may time out if noisy)."""
        name = configuration if configuration.endswith(".yaml") else f"{configuration}.yaml"
        if _use_ssh():
            result = await lab.ssh.run(
                cfg.ssh_host,
                f"timeout 25 esphome logs {shlex.quote(_yaml_path(name))} 2>&1 "
                f"| head -n {int(lines)}",
                timeout=40,
            )
            return truncate(result.stdout + result.stderr, limit)
        if not cfg.url:
            raise NotConfigured("ESPHome dashboard")
        result = await esp.run_action("logs", name, timeout=30, max_lines=lines)
        return truncate(result.get("output", ""), limit)
