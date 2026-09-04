"""MCP adapters for composite inventory operations."""

from __future__ import annotations

from typing import Any

from .._app import serialize_tool_result, with_client
from ..operations.composite import fetch_lpar_summary, fetch_system_summary
from ..tool_registry import tool_module

tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="lpar.summary", target_kind="lpar")
def hmc_lpar_summary(
    lpar_name_or_uuid: str,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any]:
    """Return state, resources, OS details, adapters, and description for one LPAR.

    Args:
        lpar_name_or_uuid: PartitionName or UUID of the logical partition.
        profile: Optional configured HMC profile name; uses the default when omitted.
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide.
    """

    async def summary(hmc):
        return serialize_tool_result(await fetch_lpar_summary(hmc, system_name_or_uuid, lpar_name_or_uuid))

    return with_client(summary, profile=profile)


@tool(effect="read", operation="system.summary", target_kind="managed_system")
def hmc_system_summary(
    system_name_or_uuid: str,
    profile: str | None = None,
) -> dict[str, Any]:
    """Return state, capacity, partition counts, and VIOS count for one system.

    Args:
        system_name_or_uuid: SystemName or UUID of the managed system.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def summary(hmc):
        return serialize_tool_result(await fetch_system_summary(hmc, system_name_or_uuid))

    return with_client(summary, profile=profile)
