"""MCP tools for the partition template library.
"""

from __future__ import annotations

from typing import Any

from ._app import (
    _READ_ONLY,
    mcp,
    with_client,
)


@mcp.tool(annotations=_READ_ONLY)
def hmc_partition_templates(
    template_uuid: str | None = None,
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """List partition templates or get one by UUID.

    - No arguments: list all partition templates in the HMC template library.
    - *template_uuid*: return full details for one template (the full config
      the template captures).
    """
    if template_uuid is not None:
        return with_client(lambda hmc: hmc.get_partition_template(template_uuid))
    return with_client(lambda hmc: hmc.list_partition_templates())


@mcp.tool
def hmc_deploy_partition_template(
    draft_template_uuid: str, target_system_uuid: str
) -> dict[str, Any] | None:
    """Deploy a partition from a *draft* partition template.

    draft_template_uuid is the transformed/replica template UUID (produced by
    capture/transform), target_system_uuid is the managed system to create the
    partition on. Submits a Deploy job; poll hmc_get_job for status.
    """

    return with_client(
        lambda hmc: hmc.deploy_partition_template(draft_template_uuid, target_system_uuid)
    )


