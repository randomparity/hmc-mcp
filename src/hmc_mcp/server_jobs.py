"""MCP tools for HMC asynchronous job inspection and waiting."""

from __future__ import annotations

from typing import Any

from ._app import _READ_ONLY, _run, mcp
from .common import client_from_env
from .errors import HMCError


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
    """List recent jobs, or an error sentinel when root Job listing is unsupported."""

    async def operation():
        async with client_from_env(profile) as hmc:
            return await hmc.list_uom("Job")

    try:
        jobs = _run(operation)
    except HMCError as exc:
        if exc.status_code != 400:
            raise
        return [
            {
                "type": "error",
                "error": (
                    "This HMC version does not support the global Job listing "
                    "endpoint. Use hmc_get_job(job_uuid, job_href=<submission link>)."
                ),
                "status_code": 400,
                "detail": str(exc),
            }
        ]
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
