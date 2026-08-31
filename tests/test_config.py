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


def test_esphome_docker_container():
    cfg = Config.from_dict(
        {
            "esphome": {
                "ssh_host": "HA",
                "docker_container": "app_5c53de3b_esphome",
                "config_dir": "/config/esphome",
            }
        }
    )
    assert cfg.esphome.enabled
    assert cfg.esphome.docker_container == "app_5c53de3b_esphome"
    assert cfg.esphome.url == ""


def test_load_env_file(tmp_path, monkeypatch):
    from homelab_mcp.__main__ import load_env_file

    env = tmp_path / "mcp.env"
    env.write_text(
        "# comment\n"
        "MCP_AUTH_TOKEN=abc123\n"
        "export PROXMOX_TOKEN_SECRET=\"s3cr3t\"\n"
        "HA_TOKEN=<paste>\n"
        "EMPTY=\n"
    )
    for k in ("MCP_AUTH_TOKEN", "PROXMOX_TOKEN_SECRET", "HA_TOKEN", "EMPTY"):
        monkeypatch.delenv(k, raising=False)
    n = load_env_file(env)
    assert n == 4
    import os

    assert os.environ["MCP_AUTH_TOKEN"] == "abc123"
    assert os.environ["PROXMOX_TOKEN_SECRET"] == "s3cr3t"
    # a value with shell metacharacters loads verbatim (no sourcing)
    assert os.environ["HA_TOKEN"] == "<paste>"


def test_load_env_file_does_not_override(tmp_path, monkeypatch):
    from homelab_mcp.__main__ import load_env_file

    env = tmp_path / "mcp.env"
    env.write_text("MCP_AUTH_TOKEN=fromfile\n")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "fromenv")
    load_env_file(env)
    import os

    assert os.environ["MCP_AUTH_TOKEN"] == "fromenv"


def test_load_env_file_missing_is_noop(tmp_path):
    from homelab_mcp.__main__ import load_env_file

    assert load_env_file(tmp_path / "nope.env") == 0


def test_fullykiosk_devices_and_shared_password():
    cfg = Config.from_dict(
        {
            "fullykiosk": {
                "password": "shared",
                "port": 2323,
                "devices": {
                    "tablet": "192.168.1.152",
                    "kuchyna": {"host": "192.168.1.153", "password": "own", "port": 2324},
                },
            }
        }
    )
    assert cfg.fullykiosk.enabled
    assert cfg.fullykiosk.devices["tablet"].host == "192.168.1.152"
    assert cfg.fullykiosk.devices["tablet"].password == "shared"
    assert cfg.fullykiosk.devices["tablet"].port == 2323
    assert cfg.fullykiosk.devices["kuchyna"].password == "own"
    assert cfg.fullykiosk.devices["kuchyna"].port == 2324
