"""MCP tools for HMC asynchronous job inspection and waiting."""

from __future__ import annotations

from typing import Any

from ._app import _READ_ONLY, _run, mcp
from .common import client_from_env
from .errors import HMCError


def _is_unsupported_job_listing(exc: HMCError) -> bool:
    body = exc.body or ""
    return (
        exc.status_code == 400
        and "REST000E" in body
        and "Unrecognized root REST type of Job" in body
    )


@mcp.tool(annotations=_READ_ONLY)
def hmc_get_job(
    job_uuid: str,
    job_href: str | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Get one HMC job by UUID, optionally using its submission SELF link."""

    async def operation():
        async with client_from_env(profile) as hmc:
            return await hmc.get_job(job_uuid, job_href=job_href)

    return _run(operation)


@mcp.tool(annotations=_READ_ONLY)
def hmc_recent_jobs(
    limit: int = 20,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List recent jobs.

    Raises HMCError when this HMC does not support global Job listing; use
    hmc_get_job with a UUID and submission link on those firmware versions.
    """

    async def operation():
        async with client_from_env(profile) as hmc:
            return await hmc.list_uom("Job")

    try:
        jobs = _run(operation)
    except HMCError as exc:
        if not _is_unsupported_job_listing(exc):
            raise
        raise HMCError(
            "This HMC version does not support global Job listing. Use "
            "hmc_get_job(job_uuid, job_href=<submission link>) instead.",
            status_code=400,
            body=exc.body,
        ) from exc
    return jobs[:limit]


@mcp.tool(annotations=_READ_ONLY)
def hmc_wait_for_job(
    job_uuid: str,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    job_href: str | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Poll until a terminal job state or timeout, returning the last job entry."""

    async def operation():
        async with client_from_env(profile) as hmc:
            return await hmc.wait_for_job(
                job_uuid,
                timeout_seconds,
                poll_interval,
                job_href=job_href,
            )

    return _run(operation)
