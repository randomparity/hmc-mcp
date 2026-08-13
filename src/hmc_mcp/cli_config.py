"""Configuration subgroup commands for hmc-mcp.

hmc-mcp config init   — create the platform-native config file
hmc-mcp config list   — list configured profile names
hmc-mcp config show   — show non-secret connection metadata for a profile
"""

from __future__ import annotations

from .cli_app import config_app  # noqa: F401  (import required; commands registered below)
