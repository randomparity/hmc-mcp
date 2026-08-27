"""MCP tool for curated fleet-health exceptions."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .._app import run_sync
from ..client.client_factory import client_from_env
from ..operations.health import fleet_health
from ..tool_registry import tool_module

tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="health.fleet", target_kind="console")
def hmc_fleet_health(profile: str | None = None) -> dict[str, Any]:
    """Return exception-only health across managed systems, partitions, and jobs.

    Args:
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def operation():
        async with client_from_env(profile) as hmc:
            return asdict(await fleet_health(hmc))

    return run_sync(operation)
