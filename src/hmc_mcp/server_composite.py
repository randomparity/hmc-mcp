"""MCP adapters for composite inventory operations."""

from __future__ import annotations

from .tool_registry import tool_module

from typing import Any

from ._app import _READ_ONLY, _run
from .common import client_from_env
from .operations_composite import lpar_summary, system_summary


tool, register_tools = tool_module()


@tool(annotations=_READ_ONLY)
def hmc_lpar_summary(
    lpar_name_or_uuid: str,
    profile: str | None = None,
) -> dict[str, Any]:
    """Return state, resources, OS details, adapters, and description for one LPAR.

    Args:
        lpar_name_or_uuid: PartitionName or UUID of the logical partition.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await lpar_summary(hmc, lpar_name_or_uuid)

    return _run(_go)


@tool(annotations=_READ_ONLY)
def hmc_system_summary(
    system_name_or_uuid: str,
    profile: str | None = None,
) -> dict[str, Any]:
    """Return state, capacity, partition counts, and VIOS count for one system.

    Args:
        system_name_or_uuid: SystemName or UUID of the managed system.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await system_summary(hmc, system_name_or_uuid)

    return _run(_go)
