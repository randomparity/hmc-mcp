"""MCP tools for HMC asynchronous job inspection and waiting."""

from __future__ import annotations

from .tool_registry import tool_module

from typing import Any

from ._app import _run, _run_limited_collection
from .common import client_from_env
from .errors import HMCError
from .jobs import JobOutcome, job_outcome


def _is_unsupported_job_listing(exc: HMCError) -> bool:
    body = exc.body or ""
    return (
        exc.status_code == 400
        and "REST000E" in body
        and "Unrecognized root REST type of Job" in body
    )


tool, register_tools, tool_security = tool_module()


# Not exhaustive: `job_href` is a caller-supplied URI whose path replaces the
# `job_uuid` selector outright — `client.get_job` fetches `urlparse(job_href).path`
# and never looks at `job_uuid`. A `targets` table would therefore authorize one
# job identity while the server reads another, so ADR 0039 grants this tool only
# under `targets = "all-targets"`. ADR 0036 already noted that a job UUID is
# minted by the HMC at runtime and so cannot usefully appear in an allowlist.
@tool(
    effect="read",
    operation="job.get",
    target_kind="job",
    exhaustive_targets=False,
)
def hmc_get_job(
    job_uuid: str,
    job_href: str | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Get one HMC job by UUID, optionally using its submission SELF link.

    Args:
        job_uuid: UUID or JobID returned when the job was submitted.
        job_href: Optional submission SELF link for firmware that cannot resolve the UUID.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def operation():
        async with client_from_env(profile) as hmc:
            return await hmc.get_job(job_uuid, job_href=job_href)

    return _run(operation)


@tool(effect="read", operation="job.list", target_kind="console")
def hmc_list_recent_jobs(
    limit: int = 20,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List recent jobs.

    Raises HMCError when this HMC does not support global Job listing; use
    hmc_get_job with a UUID and submission link on those firmware versions.

    Args:
        limit: Maximum entries returned after the complete HMC feed is transferred
            and parsed; zero returns none. This client-side cap does not reduce HMC
            work or network transfer.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """
    async def operation():
        async with client_from_env(profile) as hmc:
            return await hmc.list_uom("Job")

    try:
        return _run_limited_collection(operation, limit)
    except HMCError as exc:
        if not _is_unsupported_job_listing(exc):
            raise
        raise HMCError(
            "This HMC version does not support global Job listing. Use "
            "hmc_get_job(job_uuid, job_href=<submission link>) instead.",
            status_code=400,
            body=exc.body,
        ) from exc


# Not exhaustive: `job_href` is a caller-supplied URI whose path replaces the
# `job_uuid` selector outright — `client.get_job` fetches `urlparse(job_href).path`
# and never looks at `job_uuid`. A `targets` table would therefore authorize one
# job identity while the server reads another, so ADR 0039 grants this tool only
# under `targets = "all-targets"`. ADR 0036 already noted that a job UUID is
# minted by the HMC at runtime and so cannot usefully appear in an allowlist.
@tool(
    effect="read",
    operation="job.wait",
    target_kind="job",
    exhaustive_targets=False,
)
def hmc_wait_for_job(
    job_uuid: str,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    job_href: str | None = None,
    profile: str | None = None,
) -> JobOutcome:
    """Poll a job and return its normalized status, timeout, and error outcome.

    Polling stops at CANCELED_BEFORE_START, CANCELED_WHILE_RUNNING, COMPLETED,
    COMPLETED_OK, COMPLETED_WITH_ERROR, COMPLETED_WITH_WARNINGS, EXCEPTION,
    FAILED, FAILED_BEFORE_COMPLETION, FAILED_BEFORE_COMPLETION_RETRY, or
    FAILED_TO_START. If the timeout expires first, the last observed job is
    returned with ``timed_out`` set to true.

    Args:
        job_uuid: UUID or JobID returned when the job was submitted.
        timeout_seconds: Maximum polling duration in seconds; zero performs one poll.
        poll_interval: Seconds between polls; must be greater than zero.
        job_href: Optional submission SELF link for firmware that cannot resolve the UUID.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def operation():
        async with client_from_env(profile) as hmc:
            job = await hmc.wait_for_job(
                job_uuid,
                timeout_seconds,
                poll_interval,
                job_href=job_href,
            )
            return job_outcome(job_uuid, job)

    return _run(operation)
