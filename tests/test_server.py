import asyncio

import pytest

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


def _scope(peer_ip, headers):
    return {"client": (peer_ip, 1234), "headers": headers}


def test_xff_rightmost_untrusted_not_spoofable():
    # Attacker prepends an allowlisted IP; the real client IP is appended by the
    # proxy. The middleware must take the rightmost untrusted hop, not the left.
    mw = _mw(trusted_proxies=["127.0.0.1/32", "10.1.0.0/16"], allowed_ips=["192.168.1.0/24"])
    scope = _scope("10.1.2.3", [(b"x-forwarded-for", b"192.168.1.50, 203.0.113.9")])
    # peer 10.1.2.3 is a trusted proxy; real client is 203.0.113.9 (untrusted)
    assert mw._client_ip(scope) == "203.0.113.9"
    assert not mw._ip_allowed(mw._client_ip(scope))  # attacker's spoof is rejected


def test_xff_multiple_headers_joined():
    mw = _mw(trusted_proxies=["127.0.0.1/32"])
    scope = _scope(
        "127.0.0.1",
        [(b"x-forwarded-for", b"1.1.1.1"), (b"x-forwarded-for", b"203.0.113.9")],
    )
    # rightmost untrusted across both headers
    assert mw._client_ip(scope) == "203.0.113.9"


def test_xff_ignored_when_peer_not_trusted():
    mw = _mw(trusted_proxies=["127.0.0.1/32"], allowed_ips=["192.168.1.0/24"])
    scope = _scope("203.0.113.9", [(b"x-forwarded-for", b"192.168.1.50")])
    assert mw._client_ip(scope) == "203.0.113.9"  # peer not trusted -> XFF ignored


def test_all_hops_trusted_falls_back_to_peer():
    mw = _mw(trusted_proxies=["10.0.0.0/8"])
    scope = _scope("10.0.0.1", [(b"x-forwarded-for", b"10.0.0.2, 10.0.0.3")])
    assert mw._client_ip(scope) == "10.0.0.1"


def test_fail_closed_without_auth_on_public_bind():
    from homelab_mcp.config import Config, ConfigError
    from homelab_mcp.server import assert_safe_to_serve

    cfg = Config.from_dict({"server": {"host": "0.0.0.0", "auth_token": "", "allowed_ips": []}})
    with pytest.raises(ConfigError):
        assert_safe_to_serve(cfg)


def test_fail_closed_allows_loopback_and_optin():
    from homelab_mcp.config import Config
    from homelab_mcp.server import assert_safe_to_serve

    # loopback bind is fine without auth
    assert_safe_to_serve(Config.from_dict({"server": {"host": "127.0.0.1"}}))
    # explicit insecure opt-in is fine
    assert_safe_to_serve(
        Config.from_dict({"server": {"host": "0.0.0.0", "insecure": True}})
    )
    # a token is enough
    assert_safe_to_serve(Config.from_dict({"server": {"host": "0.0.0.0", "auth_token": "x"}}))
