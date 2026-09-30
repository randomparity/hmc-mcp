"""MCP tools for capacity reporting and placement decisions."""

from __future__ import annotations

from ..._app import with_client
from ...operations.inventory.capacity import (
    CapacitySummary,
    fetch_capacity_report,
    find_placement,
)
from ...tool_registry import tool_module

tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="capacity.report", target_kind="console")
def hmc_capacity_report(profile: str | None = None) -> list[CapacitySummary]:
    """Report assigned and available memory and processors by system.

    Figures are each system's own: total is its configurable memory (MiB) or
    processor units, free is what it currently reports available, and assigned
    is total minus free, so it includes hypervisor memory and every partition,
    VIOS included. A system that reports no capacity figure fails the report.

    Args:
        profile: Optional TOML profile name; uses environment defaults when omitted.
    """

    async def report(hmc):
        return await fetch_capacity_report(hmc)

    return with_client(report, profile=profile)


@tool(effect="read", operation="placement.find", target_kind="console")
def hmc_find_placement(
    desired_memory_mib: int,
    desired_proc_units: float = 0.5,
    profile: str | None = None,
) -> list[CapacitySummary]:
    """Rank systems able to host an LPAR with the requested capacity.

    A system qualifies when the memory and processor units it currently reports
    available cover the request. A system that reports no capacity figure fails
    the search.

    Args:
        desired_memory_mib: Required LPAR memory in MiB.
        desired_proc_units: Required shared-processor processing units.
        profile: Optional TOML profile name; uses environment defaults when omitted.
    """

    async def placements(hmc):
        return await find_placement(hmc, desired_memory_mib, desired_proc_units)

    return with_client(placements, profile=profile)
