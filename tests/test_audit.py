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
