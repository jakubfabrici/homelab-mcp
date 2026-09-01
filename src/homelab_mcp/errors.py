"""Error types shared by the tool modules."""

from __future__ import annotations

from .audit import redact


class ToolError(RuntimeError):
    """A user-facing failure: reported back to the model as text, not a traceback.

    The message is scrubbed of registered secrets on construction, because tool
    errors are surfaced verbatim to the MCP client and may interpolate an
    upstream URL or response body that echoes a credential.
    """

    def __init__(self, message: object = "") -> None:
        super().__init__(redact(str(message)))


class NotConfigured(ToolError):
    """Raised when a tool is called but its backend is not configured."""

    def __init__(self, what: str, hint: str = "") -> None:
        message = f"{what} is not configured on this MCP server."
        if hint:
            message += f" {hint}"
        super().__init__(message)
