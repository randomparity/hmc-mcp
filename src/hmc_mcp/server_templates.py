"""MCP tools for the partition template library.
"""

from __future__ import annotations

from typing import Any

from ._app import (
    _READ_ONLY,
    _run,
    mcp,
)

from .common import client_from_env



@mcp.tool(annotations=_READ_ONLY)
def hmc_list_partition_templates() -> list[dict[str, Any]]:
    """List all partition templates in the HMC template library."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_partition_templates()

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_partition_template(template_uuid: str) -> dict[str, Any] | None:
    """Get one partition template by UUID (full config the template captures)."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_partition_template(template_uuid)

    return _run(_go())


@mcp.tool
def hmc_deploy_partition_template(
    draft_template_uuid: str, target_system_uuid: str
) -> dict[str, Any] | None:
    """Deploy a partition from a *draft* partition template.

    draft_template_uuid is the transformed/replica template UUID (produced by
    capture/transform), target_system_uuid is the managed system to create the
    partition on. Submits a Deploy job; poll hmc_get_job for status.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.deploy_partition_template(draft_template_uuid, target_system_uuid)

    return _run(_go())


