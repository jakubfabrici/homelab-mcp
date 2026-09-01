import json

from homelab_mcp.audit import AuditLog, redact, register_secret


def test_redact_by_key():
    out = redact({"token": "abcdef123456", "name": "ok", "nested": {"password": "x"}})
    assert out["token"] == "***"
    assert out["nested"]["password"] == "***"
    assert out["name"] == "ok"


def test_redact_by_value():
    register_secret("supersecretvalue")
    assert redact("prefix supersecretvalue suffix") == "prefix *** suffix"


def test_audit_writes_jsonl(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.record("ssh_exec", host="pve", command="ls")
    line = path.read_text().strip()
    entry = json.loads(line)
    assert entry["event"] == "ssh_exec"
    assert entry["host"] == "pve"
    assert "ts" in entry


def test_short_secret_registered_and_redacted():
    # A 7-char Fully Kiosk / SSH password must still be scrubbed (old >=8 gate
    # silently dropped it).
    register_secret("jakubko")
    assert redact("password=jakubko sent") == "password=*** sent"


def test_audit_file_is_owner_only(tmp_path):
    import stat

    path = tmp_path / "sub" / "audit.jsonl"
    log = AuditLog(path)
    log.record("test_event", host="pve")
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600, oct(mode)


def test_tool_error_redacts_registered_secret():
    from homelab_mcp.errors import ToolError

    register_secret("supersecretvalue")
    assert "supersecretvalue" not in str(ToolError("upstream said supersecretvalue"))
