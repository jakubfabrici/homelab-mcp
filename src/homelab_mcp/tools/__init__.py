"""Tool modules; each exposes ``register(mcp, lab)``."""

from . import (
    esphome,
    fullykiosk,
    homeassistant,
    network,
    overview,
    proxmox,
    shell,
)

MODULES = (proxmox, shell, homeassistant, esphome, fullykiosk, network, overview)

__all__ = [
    "MODULES",
    "esphome",
    "fullykiosk",
    "homeassistant",
    "network",
    "overview",
    "proxmox",
    "shell",
]
