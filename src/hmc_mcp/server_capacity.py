"""MCP tools for capacity reporting and placement decisions."""

from __future__ import annotations

from typing import Any

from ._app import _READ_ONLY, _run, mcp
from .operations_capacity import capacity_report, find_placement


@mcp.tool(annotations=_READ_ONLY)
def hmc_capacity_report(profile: str | None = None) -> list[dict[str, Any]]:
    """Report assigned and available memory and processors by system."""
    return _run(lambda: capacity_report(profile))


@mcp.tool(annotations=_READ_ONLY)
def hmc_find_placement(
    desired_memory_mb: int,
    desired_proc_units: float = 0.5,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """Rank systems able to host an LPAR with the requested capacity."""
    return _run(lambda: find_placement(desired_memory_mb, desired_proc_units, profile))
