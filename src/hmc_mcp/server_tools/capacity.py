"""MCP tools for capacity reporting and placement decisions."""

from __future__ import annotations

from dataclasses import asdict
from ..tool_registry import tool_module

from typing import Any

from .._app import run_sync
from ..client.client_factory import client_from_env
from ..operations.capacity import capacity_report, find_placement


tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="capacity.report", target_kind="console")
def hmc_capacity_report(profile: str | None = None) -> list[dict[str, Any]]:
    """Report assigned and available memory and processors by system.

    Args:
        profile: Optional TOML profile name; uses environment defaults when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return [asdict(item) for item in await capacity_report(hmc)]

    return run_sync(_go)


@tool(effect="read", operation="placement.find", target_kind="console")
def hmc_find_placement(
    desired_memory_mb: int,
    desired_proc_units: float = 0.5,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """Rank systems able to host an LPAR with the requested capacity.

    Args:
        desired_memory_mb: Required LPAR memory in MiB.
        desired_proc_units: Required shared-processor processing units.
        profile: Optional TOML profile name; uses environment defaults when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return [
                asdict(item)
                for item in await find_placement(
                    hmc, desired_memory_mb, desired_proc_units
                )
            ]

    return run_sync(_go)
