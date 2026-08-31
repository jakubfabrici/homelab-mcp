import os

import pytest

from homelab_mcp.config import Config, ConfigError, expand


def test_expand_env(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    assert expand("${FOO}") == "bar"
    assert expand("${MISSING:-fallback}") == "fallback"
    assert expand({"a": ["${FOO}", 1]}) == {"a": ["bar", 1]}


def test_expand_missing_raises():
    with pytest.raises(ConfigError):
        expand("${DEFINITELY_NOT_SET_12345}")


def test_from_dict_modules_disabled_by_default():
    cfg = Config.from_dict({})
    assert cfg.proxmox.enabled is False
    assert cfg.homeassistant.enabled is False
    assert cfg.esphome.enabled is False
    assert cfg.ssh.enabled is False


def test_proxmox_enabled_and_urls():
    cfg = Config.from_dict(
        {
            "proxmox": {
                "host": "10.0.0.2",
                "token_id": "root@pam!c",
                "token_secret": "s",
            }
        }
    )
    assert cfg.proxmox.enabled
    assert cfg.proxmox.base_url == "https://10.0.0.2:8006/api2/json"


def test_ha_ws_url_derivation():
    cfg = Config.from_dict({"homeassistant": {"url": "https://ha.example.com", "token": "t"}})
    assert cfg.homeassistant.ws_url == "wss://ha.example.com/api/websocket"
    assert cfg.homeassistant.rest_url == "https://ha.example.com/api"


def test_ssh_hosts_parsed_with_defaults():
    cfg = Config.from_dict(
        {
            "ssh": {
                "default_user": "admin",
                "key_file": "/k",
                "hosts": {"pve": {"host": "10.0.0.2"}, "nas": "10.0.0.3"},
            }
        }
    )
    assert cfg.ssh.hosts["pve"].user == "admin"
    assert cfg.ssh.hosts["pve"].key_file == "/k"
    assert cfg.ssh.hosts["nas"].host == "10.0.0.3"


def test_env_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("HA_URL", "http://ha.local:8123")
    monkeypatch.setenv("HA_TOKEN", "tok")
    monkeypatch.delenv("HOMELAB_MCP_CONFIG", raising=False)
    # with no config file present, load() falls back to environment variables
    os.chdir(tmp_path)
    cfg = Config.load()
    assert cfg.homeassistant.url == "http://ha.local:8123"
    assert cfg.homeassistant.enabled
