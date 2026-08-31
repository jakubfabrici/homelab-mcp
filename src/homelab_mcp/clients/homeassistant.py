"""Async Home Assistant client: REST API plus the WebSocket registry API."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import websockets

from ..config import HomeAssistantConfig
from ..errors import NotConfigured, ToolError


class HomeAssistantClient:
    def __init__(self, config: HomeAssistantConfig) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    def _ensure_configured(self) -> None:
        if not self.config.enabled:
            raise NotConfigured(
                "Home Assistant",
                "Set homeassistant.url and homeassistant.token in homelab.yaml.",
            )

    async def client(self) -> httpx.AsyncClient:
        self._ensure_configured()
        async with self._lock:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    base_url=self.config.rest_url,
                    verify=self.config.verify_tls,
                    timeout=httpx.Timeout(60.0, connect=10.0),
                    headers={
                        "Authorization": f"Bearer {self.config.token}",
                        "Content-Type": "application/json",
                    },
                )
            return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        """Call any Home Assistant REST endpoint below ``/api``."""
        client = await self.client()
        url = "/" + path.lstrip("/")
        try:
            response = await client.request(method.upper(), url, params=params, json=json_body)
        except httpx.HTTPError as exc:
            raise ToolError(f"Home Assistant request failed: {exc}") from exc
        if response.status_code >= 400:
            raise ToolError(
                f"Home Assistant {method.upper()} {url} -> HTTP {response.status_code}: "
                f"{response.text[:600]}"
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    # -- websocket ------------------------------------------------------------

    async def ws_command(self, payload: dict[str, Any], timeout: float = 30.0) -> Any:
        """Run one WebSocket API command (used for the registries).

        The registries — areas, devices, entities — are only exposed over the
        WebSocket API, not over REST.
        """
        self._ensure_configured()
        ssl_arg: Any = None
        if self.config.ws_url.startswith("wss://") and not self.config.verify_tls:
            import ssl

            ssl_arg = ssl.create_default_context()
            ssl_arg.check_hostname = False
            ssl_arg.verify_mode = ssl.CERT_NONE

        try:
            async with asyncio.timeout(timeout):
                kwargs: dict[str, Any] = {"max_size": 32 * 1024 * 1024}
                if ssl_arg is not None:
                    kwargs["ssl"] = ssl_arg
                async with websockets.connect(self.config.ws_url, **kwargs) as socket:
                    hello = json.loads(await socket.recv())
                    if hello.get("type") != "auth_required":
                        raise ToolError(f"unexpected websocket greeting: {hello}")
                    await socket.send(
                        json.dumps({"type": "auth", "access_token": self.config.token})
                    )
                    auth = json.loads(await socket.recv())
                    if auth.get("type") != "auth_ok":
                        raise ToolError(f"Home Assistant websocket auth failed: {auth}")

                    message = {"id": 1, **payload}
                    await socket.send(json.dumps(message))
                    while True:
                        reply = json.loads(await socket.recv())
                        if reply.get("id") != 1:
                            continue
                        if not reply.get("success", True):
                            raise ToolError(
                                f"Home Assistant websocket command failed: "
                                f"{reply.get('error')}"
                            )
                        return reply.get("result")
        except TimeoutError as exc:
            raise ToolError(f"Home Assistant websocket timed out after {timeout}s") from exc
        except (OSError, websockets.WebSocketException) as exc:
            raise ToolError(f"Home Assistant websocket error: {exc}") from exc
