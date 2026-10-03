"""Prometheus instrumentation for the server.

A private :class:`CollectorRegistry` (not the library-global one) keeps tests
and repeated ``build_server`` calls independent of each other. Labels only
ever carry names the server itself defines - registered tool names, audit
event names, normalised route names - never tool arguments, hostnames,
commands or anything else a client sent, so ``/metrics`` cannot leak what the
tools were asked to do.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from importlib import metadata
from typing import Any, TypeVar

from mcp.server.fastmcp import FastMCP
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.gc_collector import GCCollector
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector

logger = logging.getLogger("homelab_mcp.metrics")

T = TypeVar("T")

UNKNOWN_TOOL = "unknown"
OTHER_PATH = "other"
HEALTH_PATH = "/health"
METRICS_PATH = "/metrics"
KNOWN_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})

# 10 ms .. 2 min: SSH/proxmox calls routinely take seconds, flashes minutes.
_DURATION_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)


def server_version() -> str:
    try:
        return metadata.version("homelab-mcp")
    except metadata.PackageNotFoundError:  # pragma: no cover - source checkout only
        return "0.0.0"


class Metrics:
    """Every collector of one server instance plus the ``/metrics`` renderer."""

    content_type = CONTENT_TYPE_LATEST

    def __init__(self, *, mcp_path: str = "/mcp", version: str | None = None) -> None:
        self.registry = CollectorRegistry()
        ProcessCollector(registry=self.registry)
        PlatformCollector(registry=self.registry)
        GCCollector(registry=self.registry)

        self.mcp_path = mcp_path.rstrip("/") or "/"
        self.tool_names: set[str] = set()

        self.info = Gauge(
            "homelab_mcp_info",
            "Build information of the running server.",
            ["version"],
            registry=self.registry,
        )
        self.info.labels(version=version or server_version()).set(1)
        self.start_time = Gauge(
            "homelab_mcp_start_time_seconds",
            "Unix time at which this server instance was built.",
            registry=self.registry,
        )
        self.start_time.set(time.time())
        self.module_enabled = Gauge(
            "homelab_mcp_module_enabled",
            "1 when the backend module is configured, 0 otherwise.",
            ["module"],
            registry=self.registry,
        )
        self.tool_calls = Counter(
            "homelab_mcp_tool_calls",
            "Tool invocations by registered tool name and outcome (ok|error).",
            ["tool", "outcome"],
            registry=self.registry,
        )
        self.tool_duration = Histogram(
            "homelab_mcp_tool_duration_seconds",
            "Wall-clock duration of tool invocations.",
            ["tool"],
            buckets=_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.tool_in_flight = Gauge(
            "homelab_mcp_tool_in_flight",
            "Tool invocations currently running.",
            ["tool"],
            registry=self.registry,
        )
        self.http_requests = Counter(
            "homelab_mcp_http_requests",
            "HTTP requests by method, normalised route and status code.",
            ["method", "path", "status"],
            registry=self.registry,
        )
        self.auth_rejections = Counter(
            "homelab_mcp_auth_rejections",
            "Requests rejected by the auth middleware, by reason (ip|token|metrics).",
            ["reason"],
            registry=self.registry,
        )
        self.audit_events = Counter(
            "homelab_mcp_audit_events",
            "Audit-log events by event name.",
            ["event"],
            registry=self.registry,
        )

    # -- label hygiene ---------------------------------------------------

    def tool_label(self, name: str) -> str:
        """Only names registered on this server may become a label value."""
        return name if name in self.tool_names else UNKNOWN_TOOL

    def path_label(self, path: str) -> str:
        """Collapse any request path to one of a handful of fixed route names."""
        normalised = path.rstrip("/") or "/"
        if normalised == self.mcp_path:
            return self.mcp_path
        if normalised in {HEALTH_PATH, "/healthz"}:
            return HEALTH_PATH
        if normalised == METRICS_PATH:
            return METRICS_PATH
        return OTHER_PATH

    @staticmethod
    def method_label(method: str) -> str:
        upper = (method or "").upper()
        return upper if upper in KNOWN_METHODS else "OTHER"

    # -- recording -------------------------------------------------------

    def set_modules(self, modules: dict[str, bool]) -> None:
        for module, enabled in modules.items():
            self.module_enabled.labels(module=module).set(1 if enabled else 0)

    def record_http(self, method: str, path: str, status: int) -> None:
        self.http_requests.labels(
            method=self.method_label(method), path=self.path_label(path), status=str(status)
        ).inc()

    def record_rejection(self, reason: str) -> None:
        self.auth_rejections.labels(reason=reason).inc()

    def record_audit(self, event: str) -> None:
        self.audit_events.labels(event=event).inc()

    async def observe_tool(self, name: str, call: Callable[[], Awaitable[T]]) -> T:
        """Run ``call`` and account for it under the tool ``name``.

        Exceptions propagate unchanged (the MCP layer turns them into error
        results); they are merely counted as ``outcome="error"``.
        """
        tool = self.tool_label(name)
        self.tool_in_flight.labels(tool=tool).inc()
        started = time.perf_counter()
        outcome = "ok"
        try:
            return await call()
        except BaseException:
            outcome = "error"
            raise
        finally:
            self.tool_duration.labels(tool=tool).observe(time.perf_counter() - started)
            self.tool_calls.labels(tool=tool, outcome=outcome).inc()
            self.tool_in_flight.labels(tool=tool).dec()

    def render(self) -> bytes:
        return generate_latest(self.registry)


class InstrumentedFastMCP(FastMCP):
    """A :class:`FastMCP` whose tool registrations and invocations feed ``Metrics``.

    Only public FastMCP entry points are overridden (``add_tool`` is what the
    ``@mcp.tool()`` decorator calls; ``call_tool`` is the handler the MCP
    protocol layer invokes for ``tools/call``), so argument validation,
    ``Context`` injection and structured output stay the library's business.
    """

    def __init__(self, *args: Any, metrics: Metrics, **kwargs: Any) -> None:
        self.metrics = metrics
        super().__init__(*args, **kwargs)

    def add_tool(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        registered = super().add_tool(fn, *args, **kwargs)
        name = (
            getattr(registered, "name", None)
            or kwargs.get("name")
            or (args[0] if args and isinstance(args[0], str) else None)
            or getattr(fn, "__name__", None)
        )
        if name:
            self.metrics.tool_names.add(str(name))
        else:  # pragma: no cover - defensive
            logger.warning("could not determine the name of a registered tool; not instrumented")
        return registered

    async def call_tool(self, name: str, *args: Any, **kwargs: Any) -> Any:
        parent = super().call_tool
        return await self.metrics.observe_tool(name, lambda: parent(name, *args, **kwargs))
