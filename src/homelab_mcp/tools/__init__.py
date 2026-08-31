"""Tool modules; each exposes ``register(mcp, lab)``."""

from . import esphome, homeassistant, network, overview, proxmox, shell

MODULES = (proxmox, shell, homeassistant, esphome, network, overview)

__all__ = ["MODULES", "esphome", "homeassistant", "network", "overview", "proxmox", "shell"]
