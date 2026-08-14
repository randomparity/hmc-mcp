"""MCP adapters for composite inventory operations."""

from __future__ import annotations

from typing import Any

from ._app import _READ_ONLY, _run, mcp
from .operations_composite import lpar_summary, system_summary


@mcp.tool(annotations=_READ_ONLY)
def hmc_lpar_summary(
    lpar_name_or_uuid: str,
    profile: str | None = None,
) -> dict[str, Any]:
    """Return state, resources, OS details, adapters, and description for one LPAR."""
    return _run(lambda: lpar_summary(lpar_name_or_uuid, profile))


@mcp.tool(annotations=_READ_ONLY)
def hmc_system_summary(
    system_name_or_uuid: str,
    profile: str | None = None,
) -> dict[str, Any]:
    """Return state, capacity, partition counts, and VIOS count for one system."""
    return _run(lambda: system_summary(system_name_or_uuid, profile))
