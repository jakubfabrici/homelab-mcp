"""Shared runtime context handed to every tool module."""

from __future__ import annotations

from dataclasses import dataclass

from .audit import AuditLog, register_secret
from .clients.esphome import ESPHomeClient
from .clients.fullykiosk import FullyKioskClient
from .clients.homeassistant import HomeAssistantClient
from .clients.proxmox import ProxmoxClient
from .clients.ssh import SSHManager
from .config import Config


@dataclass(slots=True)
class Homelab:
    """Everything the tools need: configuration, clients and the audit log."""

    config: Config
    proxmox: ProxmoxClient
    ha: HomeAssistantClient
    esphome: ESPHomeClient
    fullykiosk: FullyKioskClient
    ssh: SSHManager
    audit: AuditLog

    @classmethod
    def build(cls, config: Config) -> Homelab:
        for secret in (
            config.proxmox.token_secret,
            config.homeassistant.token,
            config.esphome.token,
            config.esphome.password,
            config.server.auth_token,
        ):
            register_secret(secret)
        for host in config.ssh.hosts.values():
            register_secret(host.password)
        for kiosk in config.fullykiosk.devices.values():
            register_secret(kiosk.password)

        return cls(
            config=config,
            proxmox=ProxmoxClient(config.proxmox),
            ha=HomeAssistantClient(config.homeassistant),
            esphome=ESPHomeClient(config.esphome),
            fullykiosk=FullyKioskClient(config.fullykiosk),
            ssh=SSHManager(config.ssh, guard_destructive=config.server.guard_destructive),
            audit=AuditLog(config.server.audit_log or None),
        )

    async def aclose(self) -> None:
        await self.proxmox.aclose()
        await self.ha.aclose()
        await self.esphome.aclose()
        await self.fullykiosk.aclose()

    def enabled_modules(self) -> dict[str, bool]:
        return {
            "proxmox": self.config.proxmox.enabled,
            "homeassistant": self.config.homeassistant.enabled,
            "esphome": self.config.esphome.enabled,
            "fullykiosk": self.config.fullykiosk.enabled,
            "ssh": self.config.ssh.enabled,
            "network": True,
        }
