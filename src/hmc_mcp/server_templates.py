"""MCP tools for the partition template library.
"""

from __future__ import annotations

from typing import Any

from ._app import (
    _READ_ONLY,
    _run,
    mcp,
)

from .client import HMCError
from .common import client_from_env


def _check_templates_error(exc: HMCError) -> None:
    """Re-raise *exc* with an actionable message for known template HTTP errors.

    HTTP 406 means partition templates are not licensed or not supported on this HMC.
    All other errors are left unchanged.

    The replacement HMCError intentionally does not forward ``body=exc.body``:
    the constructor would append the parsed HMC body text after the actionable
    message, degrading readability. ``from exc`` sets ``__cause__`` and, combined
    with the implicit ``__context__`` set by the ``except`` block, makes the
    original exception accessible in developer diagnostics.
    """
    if exc.status_code == 406:
        raise HMCError(
            "Partition templates are not licensed or not supported on this HMC. "
            "Enable the partition template feature in HMC settings or use an HMC with the feature licensed.",
            exc.status_code,
        ) from exc


@mcp.tool(annotations=_READ_ONLY)
def hmc_partition_templates(template_uuid: str | None = None, profile: str | None = None) -> Any:
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

    The result always includes an ``ownership_stamped`` key:
    - ``None`` — stamping was not attempted (``wait=False``, or the job did not
      complete, or the LPAR name was not available from the job result).
    - ``True`` — stamp succeeded (reserved for future use when LPAR name
      resolution from the job result is implemented).
    - ``False`` — stamp was attempted but failed.

    **Multi-agent ownership:** When ``wait=True`` and the job completes, the
    new LPAR's description should be stamped with an ownership token. Because
    the LPAR name is not reliably returned by the deploy job on all HMC firmware
    versions, automatic stamping is not yet implemented. After the deploy completes,
    identify the new LPAR (via ``hmc_lpars``) and call ``hmc_set_lpar_description``
    to stamp ``[hmc-mcp owner:<HMC_AGENT_ID> created:<date>]`` manually.
    """
    async def _go():
        async with client_from_env(profile) as hmc:
            try:
                job = await hmc.deploy_partition_template(draft_template_uuid, target_system_uuid)
            except HMCError as exc:
                _check_templates_error(exc)
                raise
            if not wait or job is None:
                return {
                    "job": job,
                    "ownership_stamped": None,
                    "warnings": [
                        "ownership stamp not attempted: set wait=True and identify "
                        "the new LPAR after deployment to stamp the description manually"
                    ],
                }
            job_uuid = job.get("UUID") or (job.get("Resource") or {}).get("JobID")
            if not job_uuid:
                return {
                    "job": job,
                    "ownership_stamped": None,
                    "warnings": ["ownership stamp not attempted: no job UUID in response"],
                }
            final_job = await hmc.wait_for_job(
                job_uuid, timeout_seconds, poll_interval, job_href=job.get("link")
            )
            job_status = (final_job or {}).get("Status") or (
                ((final_job or {}).get("Resource") or {}).get("Status")
            )
            # Automatic stamping is deferred: deploy job does not reliably return
            # the new LPAR name across HMC firmware versions.
            return {
                "job": final_job,
                "ownership_stamped": None,
                "warnings": [
                    f"ownership stamp not attempted: LPAR name not available from "
                    f"deploy job result (job status: {job_status!r}); "
                    "identify the new LPAR with hmc_lpars and stamp manually"
                ],
            }

    return _run(_go)
