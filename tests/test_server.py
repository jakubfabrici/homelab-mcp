import asyncio

from homelab_mcp.config import Config
from homelab_mcp.server import AuthMiddleware, build_server


def test_all_tools_registered():
    cfg = Config.from_dict({"server": {"auth_token": "x"}}, source="test")
    mcp, lab = build_server(cfg)
    try:
        tools = asyncio.run(mcp.list_tools())
    finally:
        asyncio.run(lab.aclose())
    names = {t.name for t in tools}
    for expected in [
        "homelab_overview",
        "proxmox_guests",
        "proxmox_guest_action",
        "ha_call_service",
        "esphome_flash",
        "ssh_exec",
        "docker_ps",
        "net_scan",
    ]:
        assert expected in names
    assert len(names) >= 45


def _mw(**server):
    cfg = Config.from_dict({"server": server}, source="test")
    return AuthMiddleware(app=None, config=cfg)


def test_ip_allowlist():
    mw = _mw(allowed_ips=["192.168.1.0/24"])
    assert mw._ip_allowed("192.168.1.50")
    assert not mw._ip_allowed("10.0.0.1")
    assert not mw._ip_allowed(None)


def test_no_allowlist_allows_all():
    mw = _mw()
    assert mw._ip_allowed("8.8.8.8")


def test_xff_only_trusted():
    mw = _mw(trusted_proxies=["127.0.0.1/32"])
    scope_trusted = {
        "client": ("127.0.0.1", 1234),
        "headers": [(b"x-forwarded-for", b"203.0.113.9")],
    }
    scope_untrusted = {
        "client": ("8.8.8.8", 1234),
        "headers": [(b"x-forwarded-for", b"203.0.113.9")],
    }
    assert mw._client_ip(scope_trusted) == "203.0.113.9"
    assert mw._client_ip(scope_untrusted) == "8.8.8.8"
