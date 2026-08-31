"""Small helpers shared across tool modules."""

from __future__ import annotations

from typing import Any

MAX_OUTPUT_BYTES = 200_000


def truncate(text: str, limit: int = MAX_OUTPUT_BYTES) -> str:
    """Clamp long command/log output so a single tool call cannot flood context."""
    if text is None:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    head = encoded[: limit // 2].decode("utf-8", errors="ignore")
    tail = encoded[-limit // 2 :].decode("utf-8", errors="ignore")
    dropped = len(encoded) - limit
    return f"{head}\n\n... [{dropped} bytes truncated] ...\n\n{tail}"


def compact(mapping: dict[str, Any]) -> dict[str, Any]:
    """Drop ``None`` values so tool results stay readable."""
    return {k: v for k, v in mapping.items() if v is not None}


def pick(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Project a dict down to the keys that are actually present."""
    return {k: source[k] for k in keys if k in source and source[k] is not None}


def human_bytes(value: Any) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(number) < 1024 or unit == "TiB":
            return f"{number:.1f} {unit}"
        number /= 1024
    return None


def human_uptime(seconds: Any) -> str | None:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)
