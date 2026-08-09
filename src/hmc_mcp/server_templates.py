"""MCP tools for the partition template library.
"""

from __future__ import annotations

from typing import Any

from ._app import (
    _READ_ONLY,
    _run,
    mcp,
    with_client,
)

from .common import client_from_env


@mcp.tool(annotations=_READ_ONLY)
def hmc_partition_templates(template_uuid: str | None = None) -> Any:
    """List partition templates or get one by UUID.

    When template_uuid is omitted, returns a list of all partition templates
    in the HMC template library.

    When template_uuid is provided, returns the full config dict for that one
    template, or None if not found.
    """
    if template_uuid is not None:
        return with_client(lambda hmc: hmc.get_partition_template(template_uuid))
    return with_client(lambda hmc: hmc.list_partition_templates())


@mcp.tool
def hmc_deploy_partition_template(
    draft_template_uuid: str,
    target_system_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any] | None:
    """Deploy a partition from a *draft* partition template.

    draft_template_uuid is the transformed/replica template UUID (produced by
    capture/transform), target_system_uuid is the managed system to create the
    partition on. Submits a Deploy job; poll hmc_get_job for status.

    Set wait=True to block until the job reaches a terminal state.
    """
    async def _go():
        async with client_from_env() as hmc:
            job = await hmc.deploy_partition_template(draft_template_uuid, target_system_uuid)
            if not wait or job is None:
                return job
            job_uuid = job.get("UUID") or (job.get("Resource") or {}).get("JobID")
            if not job_uuid:
                return job
            return await hmc.wait_for_job(job_uuid, timeout_seconds, poll_interval)

    return _run(_go)
