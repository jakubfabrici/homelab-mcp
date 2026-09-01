"""Client for the ESPHome dashboard add-on (HTTP + its websocket job API)."""

from __future__ import annotations

import asyncio
import json
import ssl
from typing import Any

import httpx
import websockets

from ..config import ESPHomeConfig
from ..errors import NotConfigured, ToolError

# Dashboard actions that stream their output over a websocket.
WS_ACTIONS = {"validate", "compile", "upload", "run", "clean", "logs"}


class ESPHomeClient:
    def __init__(self, config: ESPHomeConfig) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._logged_in = False

    def _ensure_configured(self) -> None:
        if not self.config.url:
            raise NotConfigured(
                "ESPHome dashboard",
                "Set esphome.url (e.g. http://homeassistant.local:6052) in homelab.yaml, "
                "or esphome.ssh_host to edit the YAML over SSH instead.",
            )

    async def client(self) -> httpx.AsyncClient:
        self._ensure_configured()
        async with self._lock:
            if self._client is None:
                headers = {}
                if self.config.token:
                    headers["Authorization"] = f"Bearer {self.config.token}"
                self._client = httpx.AsyncClient(
                    base_url=self.config.url.rstrip("/"),
                    verify=self.config.verify_tls,
                    timeout=httpx.Timeout(120.0, connect=10.0),
                    headers=headers,
                    follow_redirects=True,
                )
            if self.config.password and not self._logged_in:
                await self._login(self._client)
            return self._client

    async def _login(self, client: httpx.AsyncClient) -> None:
        data = {"password": self.config.password}
        if self.config.username:
            data["username"] = self.config.username
        try:
            response = await client.post("/login", data=data)
        except httpx.HTTPError as exc:
            raise ToolError(f"ESPHome dashboard login failed: {exc}") from exc
        if response.status_code >= 400:
            raise ToolError(
                f"ESPHome dashboard login rejected (HTTP {response.status_code})"
            )
        self._logged_in = True

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
        content: str | None = None,
    ) -> Any:
        client = await self.client()
        url = "/" + path.lstrip("/")
        try:
            response = await client.request(
                method.upper(), url, params=params, content=content
            )
        except httpx.HTTPError as exc:
            raise ToolError(f"ESPHome dashboard request failed: {exc}") from exc
        if response.status_code >= 400:
            raise ToolError(
                f"ESPHome {method.upper()} {url} -> HTTP {response.status_code}: "
                f"{response.text[:400]}"
            )
        if not response.content:
            return None
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            return response.json()
        return response.text

    async def devices(self) -> dict[str, Any]:
        """Configured (and importable) ESPHome nodes known to the dashboard."""
        payload = await self.request("GET", "/devices")
        if isinstance(payload, list):
            return {"configured": payload, "importable": []}
        return payload or {"configured": [], "importable": []}

    async def read_config(self, configuration: str) -> str:
        text = await self.request("GET", "/edit", params={"configuration": configuration})
        if not isinstance(text, str):
            raise ToolError(f"unexpected response reading {configuration}")
        return text

    async def write_config(self, configuration: str, content: str) -> None:
        await self.request(
            "POST", "/edit", params={"configuration": configuration}, content=content
        )

    async def delete_config(self, configuration: str) -> None:
        await self.request("POST", "/delete", params={"configuration": configuration})

    def _ws_url(self, action: str) -> str:
        base = self.config.url.rstrip("/")
        if base.startswith("https://"):
            return "wss://" + base[len("https://") :] + f"/{action}"
        if base.startswith("http://"):
            return "ws://" + base[len("http://") :] + f"/{action}"
        raise ToolError(f"esphome.url must be http(s): {self.config.url!r}")

    async def run_action(
        self,
        action: str,
        configuration: str,
        *,
        port: str = "OTA",
        timeout: float = 900.0,
        max_lines: int = 2000,
    ) -> dict[str, Any]:
        """Run ``validate``/``compile``/``upload``/``run`` and collect its output."""
        if action not in WS_ACTIONS:
            raise ToolError(
                f"unsupported esphome action {action!r}; use one of {sorted(WS_ACTIONS)}"
            )
        # Warm the HTTP client so a password-protected dashboard has been logged
        # in and its session cookie exists before we open the websocket — even
        # when a validate/flash is the very first ESPHome call in the process.
        await self.client()

        url = self._ws_url(action)
        headers = {}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        if self._client is not None:
            cookies = "; ".join(f"{c.name}={c.value}" for c in self._client.cookies.jar)
            if cookies:
                headers["Cookie"] = cookies

        kwargs: dict[str, Any] = {"max_size": 16 * 1024 * 1024}
        if headers:
            kwargs["additional_headers"] = headers
        if url.startswith("wss://") and not self.config.verify_tls:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            kwargs["ssl"] = context

        lines: list[str] = []
        exit_code: int | None = None
        try:
            async with asyncio.timeout(timeout):
                async with websockets.connect(url, **kwargs) as socket:
                    await socket.send(
                        json.dumps(
                            {"type": "spawn", "configuration": configuration, "port": port}
                        )
                    )
                    async for raw in socket:
                        try:
                            message = json.loads(raw)
                        except ValueError:
                            lines.append(str(raw).rstrip())
                            continue
                        event = message.get("event")
                        if event == "line":
                            if len(lines) < max_lines:
                                lines.append(str(message.get("data", "")).rstrip("\n"))
                        elif event == "exit":
                            exit_code = message.get("code")
                            break
        except TimeoutError:
            return {
                "action": action,
                "configuration": configuration,
                "timed_out": True,
                "exit_code": exit_code,
                "output": "\n".join(lines),
            }
        except (OSError, websockets.WebSocketException) as exc:
            raise ToolError(f"ESPHome {action} websocket error: {exc}") from exc

        return {
            "action": action,
            "configuration": configuration,
            "exit_code": exit_code,
            "success": exit_code == 0,
            "output": "\n".join(lines),
        }
