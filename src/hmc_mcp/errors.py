"""Client exceptions shared by the transport core and domain mixins.

The :mod:`hmc_mcp.client` package uses these exceptions from its transport
implementation in :mod:`hmc_mcp.client.core` and from its domain mixin modules.
"""

from __future__ import annotations

from defusedxml import ElementTree as DET
from defusedxml.common import DefusedXmlException

from .xmlutil import find_text


class HMCError(Exception):
    """Error returned by the HMC REST API or an HMC CLI command.

    ``HMCCLIError`` (SSH-transported CLI failures) subclasses this so callers
    can handle both paths with a single ``except HMCError``.
    """

    def __init__(
        self, message: str, status_code: int | None = None, body: str | None = None
    ):
        self.status_code = status_code
        self.body = body
        detail = message
        if status_code is not None:
            detail = f"{message} (HTTP {status_code})"
        if body:
            # HMC error bodies are XML; pull out the message if possible.
            # Fall back to raw body text if it is not valid XML.
            try:
                msg = find_text(body, "Message", "msg", "error") or body[:500]
            except (DET.ParseError, DefusedXmlException):
                msg = body[:500]
            detail = f"{detail}: {msg}"
        super().__init__(detail)


class HMCTransportError(HMCError):
    """REST request failed before the HMC returned an HTTP response."""
