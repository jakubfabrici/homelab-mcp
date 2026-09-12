"""net_ping: argv building, exit-status handling and isError reporting."""

import asyncio

import pytest

from homelab_mcp.config import Config
from homelab_mcp.errors import ToolError
from homelab_mcp.server import build_server
from homelab_mcp.tools import network
from homelab_mcp.tools.network import PING_PERMISSION_HINT, ping_command, ping_failure

PERMISSION_OUTPUT = (
    "ping: socket: Operation not permitted\n"
    "ping: => missing cap_net_raw+p capability or setuid?\n"
)
REPLY_OUTPUT = (
    "PING 192.168.1.1 (192.168.1.1) 56(84) bytes of data.\n"
    "64 bytes from 192.168.1.1: icmp_seq=1 ttl=64 time=0.412 ms\n\n"
    "--- 192.168.1.1 ping statistics ---\n"
    "1 packets transmitted, 1 received, 0% packet loss, time 0ms\n"
    "rtt min/avg/max/mdev = 0.412/0.412/0.412/0.000 ms\n"
)


def test_ping_command_is_argv_not_shell():
    argv = ping_command("192.168.1.1;id", 3)
    # the whole string stays one argv element; no shell ever interprets it
    assert argv[-1] == "192.168.1.1;id"
    assert argv[:5] == ["ping", "-c", "3", "-W", "2"]


def test_ping_command_rejects_option_like_and_bad_hosts():
    with pytest.raises(ToolError):
        ping_command("-f", 3)
    with pytest.raises(ToolError):
        ping_command("", 3)
    with pytest.raises(ToolError):
        ping_command("a b", 3)


def test_ping_command_bounds_count():
    with pytest.raises(ToolError):
        ping_command("192.168.1.1", 0)
    with pytest.raises(ToolError):
        ping_command("192.168.1.1", network.MAX_PING_COUNT + 1)
    assert ping_command("192.168.1.1", "8")[2] == "8"


def test_ping_failure_treats_no_reply_as_a_result():
    assert ping_failure("h", 0, REPLY_OUTPUT) is None
    assert ping_failure("h", 1, "100% packet loss") is None


def test_ping_failure_permission_error_is_actionable():
    message = ping_failure("192.168.1.1", 2, PERMISSION_OUTPUT)
    assert message is not None
    assert message.startswith("ping 192.168.1.1 failed: ping: socket: Operation not permitted")
    assert PING_PERMISSION_HINT in message
    assert "ping_group_range" in message and "CAP_NET_RAW" in message


def test_ping_failure_other_errors_and_via_host():
    assert ping_failure("h", 2, "ping: bad option", where="pve") == (
        "ping h on pve failed: ping: bad option"
    )
    assert ping_failure("h", 127, "") == "ping h failed: exit status 127"


class _FakeProc:
    def __init__(self, returncode: int, output: str) -> None:
        self.returncode = returncode
        self._output = output.encode()

    async def communicate(self):
        return self._output, b""

    def kill(self) -> None:  # pragma: no cover - only hit on timeout
        pass

    async def wait(self) -> None:  # pragma: no cover
        pass


def _run_net_ping(monkeypatch, returncode: int, output: str, **arguments):
    """Call net_ping through the MCP server with a stubbed ``ping`` process."""
    calls: list[tuple[str, ...]] = []

    async def fake_exec(*argv, **_kwargs):
        calls.append(argv)
        return _FakeProc(returncode, output)

    monkeypatch.setattr(network.asyncio, "create_subprocess_exec", fake_exec)
    cfg = Config.from_dict({"server": {"auth_token": "x"}}, source="test")
    mcp, lab = build_server(cfg)

    async def call():
        try:
            from mcp.shared.memory import create_connected_server_and_client_session

            async with create_connected_server_and_client_session(mcp._mcp_server) as client:
                return await client.call_tool("net_ping", {"host": "192.168.1.1", **arguments})
        finally:
            await lab.aclose()

    return asyncio.run(call()), calls


def test_net_ping_reports_socket_errors_as_is_error(monkeypatch):
    result, calls = _run_net_ping(monkeypatch, 2, PERMISSION_OUTPUT, count=8)
    assert result.isError is True
    text = result.content[0].text
    assert "Operation not permitted" in text
    assert "ping_group_range" in text
    assert calls == [("ping", "-c", "8", "-W", "2", "192.168.1.1")]


def test_net_ping_returns_transcript_on_success(monkeypatch):
    result, _ = _run_net_ping(monkeypatch, 0, REPLY_OUTPUT)
    assert result.isError is False
    assert "time=0.412 ms" in result.content[0].text


def test_net_ping_missing_binary_is_error(monkeypatch):
    async def missing(*_argv, **_kwargs):
        raise FileNotFoundError("ping")

    monkeypatch.setattr(network.asyncio, "create_subprocess_exec", missing)
    cfg = Config.from_dict({"server": {"auth_token": "x"}}, source="test")
    mcp, lab = build_server(cfg)

    async def call():
        try:
            from mcp.shared.memory import create_connected_server_and_client_session

            async with create_connected_server_and_client_session(mcp._mcp_server) as client:
                return await client.call_tool("net_ping", {"host": "192.168.1.1"})
        finally:
            await lab.aclose()

    result = asyncio.run(call())
    assert result.isError is True
    assert "iputils-ping" in result.content[0].text
