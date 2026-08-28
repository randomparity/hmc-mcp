"""MCP tools for HMC asynchronous job inspection and waiting."""

from __future__ import annotations

from ..tool_registry import tool_module

from typing import Any

from ..operations import jobs as operations_jobs
from .._app import run_sync, run_limited_collection
from ..client.client_factory import client_from_env
from ..errors import HMCError
from ..jobs import JobOutcome


def _is_unsupported_job_listing(exc: HMCError) -> bool:
    body = exc.body or ""
    return (
        exc.status_code == 400
        and "REST000E" in body
        and "Unrecognized root REST type of Job" in body
    )


tool, register_tools, tool_security = tool_module()


# Not exhaustive: `job_href` is a caller-supplied URI whose path replaces the
# `job_id` selector outright — `client.get_job` fetches `urlparse(job_href).path`
# and never looks at `job_id`. A `targets` table would therefore authorize one
# job identity while the server reads another, so ADR 0039 grants this tool only
# under `targets = "all-targets"`. ADR 0036 already noted that a job identifier
# is minted by the HMC at runtime and so cannot usefully appear in an allowlist.
@tool(
    effect="read",
    operation="job.get",
    target_kind="job",
    exhaustive_targets=False,
)
def hmc_get_job(
    job_id: str,
    job_href: str | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Get one HMC job by UUID or JobID, optionally using its submission SELF link.

    Returns null when the HMC produced no entry for this identifier — reaped,
    deleted, or never present. Any other HMC failure still raises. Null is one
    poll's answer, never a confirmed disappearance: this tool does not re-read to
    confirm, so a momentary 404 reads as null too. Null for *every* identifier is
    a deployment whose jobs path is absent, and null for one job polled with a
    ``job_href`` can be a link that stopped resolving while the job runs. Re-read
    by ``job_id`` alone before acting on a null (ADR 0093).

    An empty identifier, a bare dot, or one carrying a path, query, fragment,
    percent, or interior whitespace character is rejected outright rather than
    reported as a missing job; surrounding whitespace is trimmed. That check runs
    even when ``job_href`` is supplied.

    A ``job_href`` that stops resolving is re-read against the global jobs path
    before the job is called gone, so the ``link`` in the returned mapping can be
    the one that failed, and nothing in the result says which happened. A supplied
    link also decides **which job is read** — its path is fetched directly and
    checked only for addressing a job resource — so a mispaired handle returns the
    *other* job. Compare the returned entry's UUID or JobID against the identifier
    you passed.

    Args:
        job_id: UUID or JobID returned when the job was submitted.
        job_href: Optional submission SELF link for firmware that cannot resolve the job
            identifier.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def operation():
        async with client_from_env(profile) as hmc:
            outcome = await operations_jobs.get_job(hmc, job_id, job_href=job_href)
            return outcome.job

    return run_sync(operation)


@tool(effect="read", operation="job.list", target_kind="console")
def hmc_list_recent_jobs(
    limit: int = 20,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List recent jobs.

    Raises HMCError when this HMC does not support global Job listing; use
    hmc_get_job with a job identifier and submission link on those firmware versions.

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
        return run_limited_collection(operation, limit)
    except HMCError as exc:
        if not _is_unsupported_job_listing(exc):
            raise
        raise HMCError(
            "This HMC version does not support global Job listing. Use "
            "hmc_get_job(job_id, job_href=<submission link>) instead.",
            status_code=400,
            body=exc.body,
        ) from exc


# Not exhaustive: `job_href` is a caller-supplied URI whose path replaces the
# `job_id` selector outright — `client.get_job` fetches `urlparse(job_href).path`
# and never looks at `job_id`. A `targets` table would therefore authorize one
# job identity while the server reads another, so ADR 0039 grants this tool only
# under `targets = "all-targets"`. ADR 0036 already noted that a job identifier
# is minted by the HMC at runtime and so cannot usefully appear in an allowlist.
@tool(
    effect="read",
    operation="job.wait",
    target_kind="job",
    exhaustive_targets=False,
)
def hmc_wait_for_job(
    job_id: str,
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

    **Read ``found`` before ``timed_out``.** A job the HMC no longer has — reaped,
    deleted, or never present — returns ``found`` false immediately rather than
    raising, with a null ``status`` and ``timed_out`` true. On a ``found`` false
    outcome ``timed_out`` carries no information and never means "still running";
    only ``found`` true with ``timed_out`` true does, and a null ``status`` there
    means the entry carried no readable Status. Every HMC failure other than a
    missing job still raises ``HMCError``.

    ``found`` false is unconfirmed on the first poll: only a job that vanishes
    after this wait has seen it alive is re-read before being reported gone, so
    one momentary or empty response at the start ends the wait immediately. It is
    also not proof the work did or did not happen — confirm that against the
    affected resource, not the job record. Two shapes are not a reaped job at all:
    ``found`` false for *every* identifier is a deployment whose jobs path is
    absent, and ``found`` false for one job polled with a ``job_href`` can be a
    link that stopped resolving while the job runs. Re-read by ``job_id`` alone
    before acting on absence (ADR 0093).

    ``timeout_seconds`` is a soft bound: the confirming re-read is owed past the
    deadline, so the call can return a whole ``poll_interval`` late. Keep
    ``poll_interval`` well under ``timeout_seconds``.

    ``job_href`` on the result is the link to persist for the next call — the one
    you passed when it resolved, otherwise the successful read's own link, null
    when nothing resolved. If you passed a link and get ``found`` true with a null
    ``job_href``, that link was retired; drop it. A retired link spelled
    differently from the HMC's own SELF link can survive there anyway, so poll by
    ``job_id`` alone whenever a stored link is suspect. An echoed link is your
    own input returned verbatim — only its path is ever requested, and host,
    query, fragment and control characters are unchecked — so do not dereference
    it as something the HMC attested.

    A supplied ``job_href`` decides **which job is read**: its path is fetched
    directly and checked only for addressing a job resource, so a mispaired handle
    returns the *other* job. Compare the returned ``job_id`` against the
    identifier you passed.

    An empty identifier, a bare dot, or one carrying a path, query, fragment,
    percent, or interior whitespace character is rejected outright rather than
    reported as ``found`` false; surrounding whitespace is trimmed. That check
    runs even when ``job_href`` is supplied.

    The same two fields appear on the outcomes returned by the submit-and-wait
    tools (the migrate, remote-restart and power tools), where they describe a
    *submission* — there ``found`` false means "this submission returned no job
    entry", not "the HMC no longer has this job".

    Args:
        job_id: UUID or JobID returned when the job was submitted.
        timeout_seconds: Maximum polling duration in seconds; zero performs one poll.
        poll_interval: Seconds between polls; must be greater than zero.
        job_href: Optional submission SELF link for firmware that cannot resolve the job
            identifier.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def operation():
        async with client_from_env(profile) as hmc:
            return await operations_jobs.wait_for_job(
                hmc,
                job_id,
                job_href=job_href,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )

    return run_sync(operation)
