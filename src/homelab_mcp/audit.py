"""Audit logging and secret redaction."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("homelab_mcp.audit")

_SECRET_KEYS = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|authorization|bearer)", re.IGNORECASE
)
_SECRET_VALUES: set[str] = set()


def register_secret(value: str | None) -> None:
    """Remember a secret so it is scrubbed from any logged payload."""
    if value and len(value) >= 8:
        _SECRET_VALUES.add(value)


def redact(value: Any) -> Any:
    """Return ``value`` with secrets replaced by ``***``."""
    if isinstance(value, str):
        out = value
        for secret in _SECRET_VALUES:
            if secret in out:
                out = out.replace(secret, "***")
        return out
    if isinstance(value, dict):
        return {
            k: ("***" if isinstance(k, str) and _SECRET_KEYS.search(k) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class AuditLog:
    """Append-only JSONL audit trail of every tool invocation."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **fields: Any) -> None:
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event}
        entry.update(redact(fields))
        line = json.dumps(entry, ensure_ascii=False, default=str)
        logger.info("%s", line)
        if self.path:
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError as exc:  # pragma: no cover - disk problems only
                logger.warning("audit log write failed: %s", exc)
