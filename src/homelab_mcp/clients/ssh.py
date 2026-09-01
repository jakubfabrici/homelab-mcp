"""SSH execution against the hosts declared in the configuration."""

from __future__ import annotations

import asyncio
import re
import shlex
from dataclasses import dataclass
from typing import Any

import asyncssh

from ..config import SSHConfig, SSHHost
from ..errors import NotConfigured, ToolError

# Best-effort filter for a few obviously catastrophic commands. Only consulted
# when ``server.guard_destructive`` is enabled (default off). This is NOT a
# security boundary: free-form shell has unlimited ways to destroy a machine,
# and any configured client already holds full shell access. It exists to catch
# an accidental fat-finger, not a determined actor. The regexes also see the
# outer wrapper of docker exec / pct exec commands, so a wrapped ``rm -rf /``
# (quoted target) is matched too.
_DESTRUCTIVE = (
    # rm with BOTH a recursive and a force flag, targeting the filesystem root
    # ("/", "/*", or a quoted "/"), including when wrapped in sh -lc '...'.
    re.compile(
        r"""\brm\b
            (?=[^\n;|&]*(?:\s-\w*r|\s--recursive))   # a recursive flag somewhere
            (?=[^\n;|&]*(?:\s-\w*f|\s--force))        # a force flag somewhere
            [^\n;|&]*\s["']?/(?:\*|\s|["']|$)         # target is / or /* (opt. quoted)
        """,
        re.IGNORECASE | re.VERBOSE,
    ),
    re.compile(r"\brm\b[^\n;|&]*--no-preserve-root", re.IGNORECASE),
    re.compile(r"\bmkfs(\.\w+)?\b"),
    re.compile(r"\bdd\b[^|;]*\bof=/dev/(sd|nvme|vd|mmcblk|disk|hd)", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;:"),
    re.compile(r"\bwipefs\b"),
)


@dataclass(slots=True)
class CommandResult:
    host: str
    command: str
    exit_status: int
    stdout: str
    stderr: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "command": self.command,
            "exit_status": self.exit_status,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def looks_destructive(command: str) -> str | None:
    """Return the pattern matched if ``command`` is catastrophically destructive."""
    for pattern in _DESTRUCTIVE:
        if pattern.search(command):
            return pattern.pattern
    return None


class SSHManager:
    """Opens a connection per command; homelab hosts are few and calls are rare."""

    def __init__(self, config: SSHConfig, guard_destructive: bool = False) -> None:
        self.config = config
        self.guard_destructive = guard_destructive

    def hosts(self) -> dict[str, SSHHost]:
        return self.config.hosts

    def resolve(self, name: str) -> SSHHost:
        if not self.config.hosts:
            raise NotConfigured(
                "SSH", "Add entries under ssh.hosts in homelab.yaml to enable shell access."
            )
        if name in self.config.hosts:
            return self.config.hosts[name]
        # Allow addressing a host by IP/hostname even when it is not declared,
        # as long as at least one host entry supplies credentials.
        template = next(iter(self.config.hosts.values()))
        if re.fullmatch(r"[A-Za-z0-9_.:-]+", name):
            return SSHHost(
                name=name,
                host=name,
                user=self.config.default_user or template.user,
                port=22,
                key_file=self.config.default_key_file or template.key_file,
                password=template.password if not self.config.default_key_file else "",
                known_hosts=self.config.default_known_hosts,
                description="ad-hoc host (not declared in config)",
            )
        raise ToolError(
            f"unknown ssh host {name!r}; known hosts: {', '.join(sorted(self.config.hosts))}"
        )

    async def _connect(self, host: SSHHost) -> asyncssh.SSHClientConnection:
        options: dict[str, Any] = {
            "host": host.host,
            "port": host.port,
            "username": host.user,
            "known_hosts": host.known_hosts or None,
            "connect_timeout": self.config.connect_timeout,
        }
        if host.key_file:
            options["client_keys"] = [host.key_file]
        if host.password:
            options["password"] = host.password
            options["client_keys"] = options.get("client_keys") or None
        try:
            return await asyncssh.connect(**options)
        except (OSError, asyncssh.Error) as exc:
            raise ToolError(
                f"ssh connect to {host.name} ({host.host}:{host.port}) failed: {exc}"
            ) from exc

    async def run(
        self,
        host_name: str,
        command: str,
        *,
        timeout: int | None = None,
        stdin: str | None = None,
        check: bool = False,
    ) -> CommandResult:
        host = self.resolve(host_name)
        if self.guard_destructive and (pattern := looks_destructive(command)):
            raise ToolError(
                f"refusing command: matches destructive pattern {pattern!r}. "
                "Set server.guard_destructive: false to allow it."
            )
        timeout = timeout or self.config.command_timeout
        connection = await self._connect(host)
        try:
            async with asyncio.timeout(timeout):
                completed = await connection.run(command, input=stdin, check=False)
        except TimeoutError as exc:
            raise ToolError(f"command on {host.name} timed out after {timeout}s") from exc
        except asyncssh.Error as exc:
            raise ToolError(f"command on {host.name} failed: {exc}") from exc
        finally:
            connection.close()

        result = CommandResult(
            host=host.name,
            command=command,
            exit_status=completed.exit_status if completed.exit_status is not None else -1,
            stdout=(completed.stdout or "") if isinstance(completed.stdout, str) else "",
            stderr=(completed.stderr or "") if isinstance(completed.stderr, str) else "",
        )
        if check and result.exit_status != 0:
            raise ToolError(
                f"command on {host.name} exited {result.exit_status}: "
                f"{(result.stderr or result.stdout).strip()[:600]}"
            )
        return result

    async def read_file(self, host_name: str, path: str) -> str:
        result = await self.run(host_name, f"cat -- {shlex.quote(path)}", check=True)
        return result.stdout

    async def write_file(self, host_name: str, path: str, content: str) -> CommandResult:
        quoted = shlex.quote(path)
        # Double-quote the command substitution so a directory containing a
        # space or glob is passed to mkdir as a single argument, not word-split.
        script = f'mkdir -p -- "$(dirname {quoted})" && cat > {quoted}'
        return await self.run(host_name, script, stdin=content, check=True)
