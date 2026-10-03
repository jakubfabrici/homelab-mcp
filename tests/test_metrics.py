"""Prometheus instrumentation: tool accounting and the gated /metrics endpoint."""

from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from homelab_mcp.config import Config, ConfigError
from homelab_mcp.metrics import Metrics
from homelab_mcp.server import AuthMiddleware, build_server

SCRAPER = "192.168.1.230"


def _build(**server):
    cfg = Config.from_dict({"server": {"auth_token": "master", **server}}, source="test")
    mcp, lab = build_server(cfg)
    return mcp, lab, cfg


def _sample(mcp, name, **labels):
    return mcp.metrics.registry.get_sample_value(name, labels)


# --------------------------------------------------------------------- tools


async def test_tool_call_is_counted_and_timed():
    mcp, lab, _ = _build()
    try:
        assert "ssh_hosts" in mcp.metrics.tool_names
        await mcp.call_tool("ssh_hosts", {})
        assert _sample(mcp, "homelab_mcp_tool_calls_total", tool="ssh_hosts", outcome="ok") == 1
        assert _sample(mcp, "homelab_mcp_tool_duration_seconds_count", tool="ssh_hosts") == 1
        assert _sample(mcp, "homelab_mcp_tool_in_flight", tool="ssh_hosts") == 0
    finally:
        await lab.aclose()


async def test_failing_tool_counts_error_and_reraises():
    mcp, lab, _ = _build()
    try:
        with pytest.raises(ToolError):
            # ssh module is not configured -> the tool raises
            await mcp.call_tool("ssh_exec", {"host": "nope", "command": "true"})
        assert _sample(mcp, "homelab_mcp_tool_calls_total", tool="ssh_exec", outcome="error") == 1
        assert _sample(mcp, "homelab_mcp_tool_calls_total", tool="ssh_exec", outcome="ok") is None
        assert _sample(mcp, "homelab_mcp_tool_in_flight", tool="ssh_exec") == 0
    finally:
        await lab.aclose()


async def test_unknown_tool_name_never_becomes_a_label():
    mcp, lab, _ = _build()
    try:
        with pytest.raises(ToolError):
            await mcp.call_tool("rm_rf_slash; DROP TABLE", {})
        assert _sample(mcp, "homelab_mcp_tool_calls_total", tool="unknown", outcome="error") == 1
        assert b"DROP TABLE" not in mcp.metrics.render()
    finally:
        await lab.aclose()


async def test_audit_events_are_counted():
    mcp, lab, _ = _build()
    try:
        before = _sample(mcp, "homelab_mcp_audit_events_total", event="server_start")
        assert before == 1  # recorded by build_server
        lab.audit.record("ha_call_service", domain="light", service="turn_on")
        assert _sample(mcp, "homelab_mcp_audit_events_total", event="ha_call_service") == 1
    finally:
        await lab.aclose()


def test_two_servers_have_independent_registries():
    mcp1, lab1, _ = _build()
    mcp2, lab2, _ = _build()
    try:
        assert mcp1.metrics.registry is not mcp2.metrics.registry
        assert _sample(mcp1, "homelab_mcp_module_enabled", module="network") == 1
        assert _sample(mcp2, "homelab_mcp_module_enabled", module="proxmox") == 0
    finally:
        asyncio.run(lab1.aclose())
        asyncio.run(lab2.aclose())


# ------------------------------------------------------------------ endpoint


class _Scraper:
    """Full ASGI stack (streamable HTTP app + AuthMiddleware) with a chosen client IP."""

    def __init__(self, client_ip: str = "203.0.113.9", **server):
        self.mcp, self.lab, cfg = _build(**server)
        app = self.mcp.streamable_http_app()
        app.add_middleware(AuthMiddleware, config=cfg, metrics=self.mcp.metrics)
        self.client = TestClient(app, client=(client_ip, 51234))

    def __enter__(self):
        self.client.__enter__()
        return self

    def __exit__(self, *exc):
        self.client.__exit__(*exc)
        asyncio.run(self.lab.aclose())


def test_metrics_allowed_by_scraper_ip():
    with _Scraper(client_ip=SCRAPER, metrics_allowed_ips=[f"{SCRAPER}/32"]) as s:
        resp = s.client.get("/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        body = resp.text
        assert "homelab_mcp_info{" in body
        assert "process_start_time_seconds" in body
        assert "homelab_mcp_module_enabled{" in body


def test_metrics_allowed_by_metrics_token_and_master_token():
    with _Scraper(metrics_token="scrape-only") as s:
        assert s.client.get("/metrics").status_code == 403
        ok = s.client.get("/metrics", headers={"Authorization": "Bearer scrape-only"})
        assert ok.status_code == 200
        master = s.client.get("/metrics", headers={"Authorization": "Bearer master"})
        assert master.status_code == 200
        bad = s.client.get("/metrics", headers={"Authorization": "Bearer scrape-onlx"})
        assert bad.status_code == 403
        assert _sample(s.mcp, "homelab_mcp_auth_rejections_total", reason="metrics") == 2


def test_metrics_closed_by_default_and_not_spoofable():
    # No scraper IP, no metrics token: only the master token opens /metrics.
    with _Scraper(trusted_proxies=["127.0.0.1/32"]) as s:
        assert s.client.get("/metrics").status_code == 403
        assert s.client.get("/metrics/").status_code == 403
        assert s.client.head("/metrics").status_code == 403
        assert s.client.post("/metrics").status_code == 403
        master = {"Authorization": "Bearer master"}
        assert s.client.get("/metrics", headers=master).status_code == 200
    # The metrics allowlist must not be fooled by a forged X-Forwarded-For from
    # an untrusted peer, and the metrics token must not open /mcp.
    with _Scraper(metrics_allowed_ips=[f"{SCRAPER}/32"], metrics_token="scrape-only") as s:
        forged = s.client.get("/metrics", headers={"X-Forwarded-For": SCRAPER})
        assert forged.status_code == 403
        mcp_resp = s.client.post("/mcp", headers={"Authorization": "Bearer scrape-only"})
        assert mcp_resp.status_code == 401


def test_metrics_allowlist_honours_trusted_proxy_chain():
    with _Scraper(
        client_ip="10.0.0.1",
        trusted_proxies=["10.0.0.0/8"],
        metrics_allowed_ips=[f"{SCRAPER}/32"],
    ) as s:
        via_proxy = s.client.get("/metrics", headers={"X-Forwarded-For": SCRAPER})
        assert via_proxy.status_code == 200
        spoofed = s.client.get("/metrics", headers={"X-Forwarded-For": f"{SCRAPER}, 8.8.8.8"})
        assert spoofed.status_code == 403


def test_health_stays_open_and_http_counter_has_fixed_labels_only():
    with _Scraper() as s:
        assert s.client.get("/health").text == "ok"
        s.client.get("/metrics")
        s.client.get("/definitely/not/a/route?x=secret-ish")
        s.client.request("BREW", "/health")
        body = s.client.get("/metrics", headers={"Authorization": "Bearer master"}).text
    assert "definitely" not in body and "secret-ish" not in body and "BREW" not in body
    assert 'homelab_mcp_http_requests_total{method="GET",path="/health",status="200"}' in body
    assert 'path="other"' in body
    assert 'method="OTHER"' in body


def test_metrics_token_registered_as_secret():
    from homelab_mcp.audit import redact

    _mcp, lab, _ = _build(metrics_token="super-secret-scrape")
    try:
        assert redact("token super-secret-scrape here") == "token *** here"
    finally:
        asyncio.run(lab.aclose())


def test_metrics_token_must_be_a_string():
    with pytest.raises(ConfigError):
        Config.from_dict({"server": {"auth_token": "x", "metrics_token": False}})


def test_path_and_method_labels():
    m = Metrics(mcp_path="/mcp")
    assert m.path_label("/mcp/") == "/mcp"
    assert m.path_label("/healthz") == "/health"
    assert m.path_label("/metrics") == "/metrics"
    assert m.path_label("/Metrics") == "other"
    assert m.path_label("/anything/else") == "other"
    assert m.method_label("get") == "GET"
    assert m.method_label("BREW") == "OTHER"
