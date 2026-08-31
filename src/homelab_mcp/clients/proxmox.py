"""Async Proxmox VE API client (API-token auth)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..config import ProxmoxConfig
from ..errors import NotConfigured, ToolError


class ProxmoxClient:
    """Minimal wrapper over the Proxmox VE ``/api2/json`` REST API.

    Everything the API offers is reachable through :meth:`request`; the named
    helpers exist only because they are the calls used constantly.
    """

    def __init__(self, config: ProxmoxConfig) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    def _ensure_configured(self) -> None:
        if not self.config.enabled:
            raise NotConfigured(
                "Proxmox",
                "Set proxmox.host, proxmox.token_id and proxmox.token_secret in homelab.yaml.",
            )

    async def client(self) -> httpx.AsyncClient:
        self._ensure_configured()
        async with self._lock:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    base_url=self.config.base_url,
                    verify=self.config.verify_tls,
                    timeout=httpx.Timeout(30.0, connect=10.0),
                    headers={
                        "Authorization": (
                            f"PVEAPIToken={self.config.token_id}={self.config.token_secret}"
                        )
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
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Call any Proxmox API endpoint and return its ``data`` payload."""
        client = await self.client()
        url = "/" + path.lstrip("/")
        try:
            response = await client.request(
                method.upper(), url, params=params, data=data
            )
        except httpx.HTTPError as exc:
            raise ToolError(f"Proxmox request failed: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text.strip()
            raise ToolError(
                f"Proxmox {method.upper()} {url} -> HTTP {response.status_code}: {detail[:600]}"
            )
        if not response.content:
            return None
        try:
            return response.json().get("data")
        except ValueError:
            return response.text

    # -- convenience wrappers -------------------------------------------------

    async def nodes(self) -> list[dict[str, Any]]:
        return await self.request("GET", "/nodes") or []

    async def cluster_resources(self, kind: str | None = None) -> list[dict[str, Any]]:
        params = {"type": kind} if kind else None
        return await self.request("GET", "/cluster/resources", params=params) or []

    async def guests(self) -> list[dict[str, Any]]:
        """Every VM and LXC in the cluster, discovered live on each call."""
        resources = await self.cluster_resources()
        return [r for r in resources if r.get("type") in {"qemu", "lxc"}]

    async def find_guest(self, vmid: int | str) -> dict[str, Any]:
        vmid = int(vmid)
        for guest in await self.guests():
            if int(guest.get("vmid", -1)) == vmid:
                return guest
        raise ToolError(f"no VM or container with vmid {vmid} found in the cluster")

    @staticmethod
    def guest_path(guest: dict[str, Any]) -> str:
        kind = "qemu" if guest.get("type") == "qemu" else "lxc"
        return f"/nodes/{guest['node']}/{kind}/{int(guest['vmid'])}"

    async def wait_for_task(
        self, node: str, upid: str, timeout: float = 120.0, poll: float = 1.0
    ) -> dict[str, Any]:
        """Block until a Proxmox task finishes (or the timeout elapses)."""
        deadline = asyncio.get_running_loop().time() + timeout
        status: dict[str, Any] = {}
        while asyncio.get_running_loop().time() < deadline:
            status = await self.request("GET", f"/nodes/{node}/tasks/{upid}/status") or {}
            if status.get("status") == "stopped":
                return status
            await asyncio.sleep(poll)
        status["timed_out"] = True
        return status
