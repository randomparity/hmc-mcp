"""Stable connection boundary for reusable hmc-mcp library consumers.

Domain operations and their result types live in their owning modules. They are
available to pre-release consumers who explicitly choose those module paths,
but only the connection, configuration, and common-error names below are part
of this compatibility contract.
"""

from hmcpctl.client.core import HMCClient, TLSVerificationDisabledWarning
from hmcpctl.config import ConfigError, HMCConfig
from hmcpctl.errors import HMCError, HMCTransportError

__all__ = [  # noqa: RUF022 - ordered to match ADR 0118's connection contract
    "HMCClient",
    "HMCConfig",
    "ConfigError",
    "HMCError",
    "HMCTransportError",
    "TLSVerificationDisabledWarning",
]
