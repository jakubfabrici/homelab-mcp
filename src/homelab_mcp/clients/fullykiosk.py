"""Client for Fully Kiosk Browser's Remote Admin REST API.

Fully Kiosk exposes a simple HTTP command API (default port 2323) guarded by
the Remote Admin password: ``http://<ip>:2323/?cmd=<command>&password=<pw>``.
Most commands return JSON; ``getScreenshot`` returns a PNG.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..config import FullyKioskConfig, FullyKioskDevice
from ..errors import NotConfigured, ToolError


class FullyKioskClient:
    def __init__(self, config: FullyKioskConfig) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    def _ensure_configured(self) -> None:
        if not self.config.enabled:
            raise NotConfigured(
                "Fully Kiosk",
                "Add fullykiosk.devices (and a password) in homelab.yaml, and enable "
                "Remote Administration in the Fully Kiosk app settings.",
            )

    def resolve(self, device: str | None) -> FullyKioskDevice:
        self._ensure_configured()
        if device is None:
            return next(iter(self.config.devices.values()))
        if device in self.config.devices:
            return self.config.devices[device]
        raise ToolError(
            f"unknown fully kiosk device {device!r}; known: {', '.join(self.config.devices)}"
        )

    async def client(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=8.0))
            return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def command(
        self, device: str | None, cmd: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Run a Remote Admin command and return JSON, text, or a note for binary."""
        dev = self.resolve(device)
        client = await self.client()
        query: dict[str, Any] = {"cmd": cmd, "type": "json", "password": dev.password}
        if params:
            query.update({k: v for k, v in params.items() if v is not None})
        url = f"http://{dev.host}:{dev.port}/"
        try:
            response = await client.get(url, params=query)
        except httpx.HTTPError as exc:
            raise ToolError(f"Fully Kiosk request to {dev.host} failed: {exc}") from exc
        if response.status_code >= 400:
            raise ToolError(
                f"Fully Kiosk {cmd} on {dev.host} -> HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            data = response.json()
            if isinstance(data, dict) and data.get("status") == "Error":
                raise ToolError(f"Fully Kiosk error: {data.get('statustext', data)}")
            return data
        if content_type.startswith(("image/", "application/octet-stream")):
            return {"binary": True, "content_type": content_type, "bytes": len(response.content)}
        return response.text

    async def screenshot(self, device: str | None) -> bytes:
        dev = self.resolve(device)
        client = await self.client()
        url = f"http://{dev.host}:{dev.port}/"
        try:
            response = await client.get(
                url, params={"cmd": "getScreenshot", "password": dev.password}
            )
        except httpx.HTTPError as exc:
            raise ToolError(f"Fully Kiosk screenshot failed: {exc}") from exc
        if response.status_code >= 400 or not response.headers.get(
            "content-type", ""
        ).startswith("image/"):
            raise ToolError(
                f"Fully Kiosk screenshot on {dev.host} did not return an image "
                f"(HTTP {response.status_code}); is Remote Admin enabled?"
            )
        return response.content
