"""MCP tool for curated fleet-health exceptions."""

from __future__ import annotations

from typing import Any

from .._app import serialize_tool_result, with_client
from ..operations.health import fetch_fleet_health
from ..tool_registry import tool_module

tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="health.fleet", target_kind="console")
def hmc_fleet_health(profile: str | None = None) -> dict[str, Any]:
    """Return exception-only health across managed systems, partitions, and jobs.

    Args:
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def health(hmc):
        return serialize_tool_result(await fetch_fleet_health(hmc))

    return with_client(health, profile=profile)
