"""MCP tools for the partition template library."""

from __future__ import annotations

from typing import Any

from ._app import (
    _READ_ONLY,
    _run,
    mcp,
)

from .errors import HMCError
from .common import client_from_env
from .jobs import wait_for_submitted_job

_MANUAL_STAMP_WARNING = (
    "ownership stamp not attempted: template deployment does not identify and stamp "
    "the new LPAR; identify it with hmc_lpars and call hmc_set_lpar_description"
)


def _check_templates_error(exc: HMCError) -> None:
    """Translate unsupported partition-template errors without response bodies."""
    if exc.status_code == 406:
        raise HMCError(
            "Partition templates are not licensed or not supported on this HMC. "
            "Enable the partition template feature in HMC settings or use an HMC with the feature licensed.",
            exc.status_code,
        ) from exc


@mcp.tool(annotations=_READ_ONLY)
def hmc_partition_templates(
    template_uuid: str | None = None, profile: str | None = None
) -> list[dict[str, Any]] | dict[str, Any] | None:
    """List partition templates or get one by UUID.

    When template_uuid is omitted, returns a list of all partition templates
    in the HMC template library.

    When template_uuid is provided, returns the full config dict for that one
    template, or None if not found.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            try:
                if template_uuid is not None:
                    return await hmc.get_partition_template(template_uuid)
                return await hmc.list_partition_templates()
            except HMCError as exc:
                _check_templates_error(exc)
                raise

    return _run(_go)


@mcp.tool
def hmc_deploy_partition_template(
    draft_template_uuid: str,
    target_system_uuid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any]:
    """Deploy a partition from a *draft* partition template.

    draft_template_uuid is the transformed/replica template UUID (produced by
    capture/transform), target_system_uuid is the managed system to create the
    partition on. Submits a Deploy job; poll hmc_get_job for status.

    Set wait=True to block until the job reaches a terminal state.

    Template deployment cannot identify the new LPAR consistently across HMC
    firmware, so it returns a warning directing callers to identify and stamp
    the new partition manually.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            try:
                job = await hmc.deploy_partition_template(
                    draft_template_uuid, target_system_uuid
                )
            except HMCError as exc:
                _check_templates_error(exc)
                raise
            selected_job = await wait_for_submitted_job(
                hmc, job, wait, timeout_seconds, poll_interval
            )
            return {
                "job": selected_job,
                "warnings": [_MANUAL_STAMP_WARNING],
            }

    return _run(_go)
