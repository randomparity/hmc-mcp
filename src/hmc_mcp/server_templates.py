"""MCP tools for the partition template library."""

from __future__ import annotations

from typing import Any

from ._app import (
    _READ_ONLY,
    _run,
    mcp,
)

from .errors import HMCError
from .error_translation import translate_template_error
from .common import client_from_env
from .operations_templates import deploy_partition_template


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_partition_templates(profile: str | None = None) -> list[dict[str, Any]]:
    """List all partition templates in the HMC template library."""

    async def _go():
        async with client_from_env(profile) as hmc:
            try:
                return await hmc.list_partition_templates()
            except HMCError as exc:
                translate_template_error(exc)
                raise

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_partition_template(
    template_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Get one partition template by UUID.

    Returns None only for an empty successful response. HTTP 404 and other
    REST failures raise HMCError.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            try:
                return await hmc.get_partition_template(template_uuid)
            except HMCError as exc:
                translate_template_error(exc)
                raise

    return _run(_go)


@mcp.tool
def hmc_deploy_partition_template(
    draft_template_uuid: str,
    target_system_name_or_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any]:
    """Deploy a partition from a *draft* partition template.

    draft_template_uuid is the transformed/replica template UUID (produced by
    capture/transform). The target managed system accepts a name or UUID.
    Submits a Deploy job; poll hmc_get_job for status.

    Set wait=True to block until the job reaches a terminal state.

    Template deployment cannot identify the new LPAR consistently across HMC
    firmware, so it returns a warning directing callers to identify and stamp
    the new partition manually.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await deploy_partition_template(
                hmc,
                draft_template_uuid,
                target_system_name_or_uuid,
                wait=wait,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )

    return _run(_go)
