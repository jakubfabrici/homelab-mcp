"""Error types shared by the tool modules."""

from __future__ import annotations


class ToolError(RuntimeError):
    """A user-facing failure: reported back to the model as text, not a traceback."""


class NotConfigured(ToolError):
    """Raised when a tool is called but its backend is not configured."""

    def __init__(self, what: str, hint: str = "") -> None:
        message = f"{what} is not configured on this MCP server."
        if hint:
            message += f" {hint}"
        super().__init__(message)
