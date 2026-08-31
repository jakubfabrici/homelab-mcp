"""Fully Kiosk Browser tools (Remote Admin REST API on Android tablets/kiosks)."""

from __future__ import annotations

import base64
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent

from ..context import Homelab


def register(mcp: FastMCP, lab: Homelab) -> None:
    fk = lab.fullykiosk

    @mcp.tool()
    async def fullykiosk_devices() -> list[dict[str, Any]]:
        """List the Fully Kiosk devices this server can control."""
        return [
            {"name": d.name, "host": d.host, "port": d.port, "description": d.description}
            for d in fk.config.devices.values()
        ]

    @mcp.tool()
    async def fullykiosk_info(device: str | None = None) -> Any:
        """Device info & status (model, battery, screen state, current URL, ...).

        Args:
            device: device name from fullykiosk_devices; omit for the first one.
        """
        return await fk.command(device, "deviceInfo")

    @mcp.tool()
    async def fullykiosk_screenshot(device: str | None = None) -> ImageContent:
        """Capture what is currently on the tablet's screen."""
        lab.audit.record("fullykiosk_screenshot", device=device)
        png = await fk.screenshot(device)
        return ImageContent(
            type="image",
            data=base64.b64encode(png).decode("ascii"),
            mimeType="image/png",
        )

    @mcp.tool()
    async def fullykiosk_load_url(url: str, device: str | None = None) -> Any:
        """Load a URL in the kiosk browser.

        Args:
            url: the URL to open.
            device: device name; omit for the first one.
        """
        lab.audit.record("fullykiosk_load_url", device=device, url=url)
        return await fk.command(device, "loadURL", {"url": url})

    @mcp.tool()
    async def fullykiosk_screen(on: bool, device: str | None = None) -> Any:
        """Turn the screen on or off."""
        lab.audit.record("fullykiosk_screen", device=device, on=on)
        return await fk.command(device, "screenOn" if on else "screenOff")

    @mcp.tool()
    async def fullykiosk_brightness(level: int, device: str | None = None) -> Any:
        """Set screen brightness (0-255)."""
        level = max(0, min(255, int(level)))
        lab.audit.record("fullykiosk_brightness", device=device, level=level)
        return await fk.command(
            device, "setStringSetting", {"key": "screenBrightness", "value": level}
        )

    @mcp.tool()
    async def fullykiosk_say(text: str, device: str | None = None, locale: str = "sk_SK") -> Any:
        """Speak text on the device via text-to-speech.

        Args:
            text: what to say.
            device: device name; omit for the first one.
            locale: TTS locale, e.g. "sk_SK" or "en_US".
        """
        lab.audit.record("fullykiosk_say", device=device, text=text)
        return await fk.command(device, "textToSpeech", {"text": text, "locale": locale})

    @mcp.tool()
    async def fullykiosk_restart(device: str | None = None) -> Any:
        """Restart the Fully Kiosk app on the device."""
        lab.audit.record("fullykiosk_restart", device=device)
        return await fk.command(device, "restartApp")

    @mcp.tool()
    async def fullykiosk_to_foreground(device: str | None = None) -> Any:
        """Bring Fully Kiosk back to the foreground (e.g. after another app took over)."""
        return await fk.command(device, "toForeground")

    @mcp.tool()
    async def fullykiosk_command(
        cmd: str, params: dict[str, Any] | None = None, device: str | None = None
    ) -> Any:
        """Run any Fully Kiosk Remote Admin command (escape hatch).

        See the Fully Kiosk REST API reference for command names, e.g.
        "setAudioVolume" (level, stream), "playSound" (url), "stopSound",
        "setBooleanSetting"/"setStringSetting" (key, value), "triggerMotion",
        "clearCache", "getDeviceInfo".

        Args:
            cmd: the command name.
            params: extra query parameters for the command.
            device: device name; omit for the first one.
        """
        lab.audit.record("fullykiosk_command", device=device, cmd=cmd, params=params)
        return await fk.command(device, cmd, params)
