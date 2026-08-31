"""Assemble the FastMCP server and its authenticated HTTP transport."""

from __future__ import annotations

import ipaddress
import logging

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Config
from .context import Homelab
from .tools import MODULES

logger = logging.getLogger("homelab_mcp")


class AuthMiddleware:
    """Bearer-token auth plus an optional IP allowlist, applied to /mcp.

    The health endpoint stays open so a reverse proxy can probe it.  Client IPs
    are taken from ``X-Forwarded-For`` only when the immediate peer is a
    configured trusted proxy, so the allowlist cannot be spoofed by a header.
    """

    def __init__(self, app: ASGIApp, config: Config) -> None:
        self.app = app
        self.token = config.server.auth_token
        self.path = config.server.path
        self.allowed = [ipaddress.ip_network(c, strict=False) for c in config.server.allowed_ips]
        self.trusted = [
            ipaddress.ip_network(c, strict=False) for c in config.server.trusted_proxies
        ]

    def _client_ip(self, scope: Scope) -> str | None:
        peer = scope.get("client")
        peer_ip = peer[0] if peer else None
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        forwarded = headers.get("x-forwarded-for")
        if forwarded and peer_ip and self._is_trusted(peer_ip):
            return forwarded.split(",")[0].strip()
        return peer_ip

    def _is_trusted(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in network for network in self.trusted)

    def _ip_allowed(self, ip: str | None) -> bool:
        if not self.allowed:
            return True
        if ip is None:
            return False
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in network for network in self.allowed)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path.rstrip("/") in {"/health", "/healthz"} or path == "/":
            await self.app(scope, receive, send)
            return

        client_ip = self._client_ip(scope)
        if not self._ip_allowed(client_ip):
            logger.warning("rejected %s from disallowed ip %s", path, client_ip)
            await JSONResponse({"error": "forbidden"}, status_code=403)(scope, receive, send)
            return

        if self.token:
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            provided = headers.get("authorization", "")
            expected = f"Bearer {self.token}"
            # constant-time-ish comparison
            if not _secure_eq(provided, expected):
                logger.warning("rejected %s from %s: bad token", path, client_ip)
                await JSONResponse({"error": "unauthorized"}, status_code=401)(
                    scope, receive, send
                )
                return

        await self.app(scope, receive, send)


def _secure_eq(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a.encode(), b.encode())


def build_server(config: Config) -> tuple[FastMCP, Homelab]:
    lab = Homelab.build(config)
    instructions = (
        "Tools for operating a home lab: a Proxmox VE cluster (VMs and LXC "
        "containers, discovered live), Home Assistant (states, services, "
        "automations, ESPHome devices), shell/Docker access to hosts over SSH, "
        "and network discovery. Call homelab_overview first to see what is "
        "configured and reachable."
    )
    mcp = FastMCP(
        name="homelab",
        instructions=instructions,
        host=config.server.host,
        port=config.server.port,
        streamable_http_path=config.server.path,
        stateless_http=True,
        json_response=True,
    )

    for module in MODULES:
        module.register(mcp, lab)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    lab.audit.record(
        "server_start",
        modules=lab.enabled_modules(),
        auth=bool(config.server.auth_token),
        allowlist=config.server.allowed_ips,
    )
    return mcp, lab


def run(config: Config) -> None:
    logging.basicConfig(
        level=getattr(logging, config.server.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not config.server.auth_token:
        logger.warning(
            "MCP_AUTH_TOKEN is empty: the server has NO authentication. "
            "Only acceptable behind a proxy that adds its own auth."
        )

    mcp, lab = build_server(config)
    app = mcp.streamable_http_app()
    app.add_middleware(AuthMiddleware, config=config)

    import uvicorn

    logger.info(
        "homelab-mcp listening on %s:%s%s (modules: %s)",
        config.server.host,
        config.server.port,
        config.server.path,
        ", ".join(k for k, v in lab.enabled_modules().items() if v),
    )
    try:
        uvicorn.run(
            app,
            host=config.server.host,
            port=config.server.port,
            log_level=config.server.log_level.lower(),
            access_log=False,
        )
    finally:
        import asyncio

        try:
            asyncio.run(lab.aclose())
        except RuntimeError:
            pass
