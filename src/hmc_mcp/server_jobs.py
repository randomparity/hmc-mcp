"""MCP tools for HMC asynchronous job inspection and waiting."""

from __future__ import annotations

from .tool_registry import tool_module

from typing import Any

from . import operations_jobs
from ._app import _run, _run_limited_collection
from .common import client_from_env
from .errors import HMCError
from .jobs import JobOutcome


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

    Returns null when the HMC produced no entry for this identifier — reaped,
    deleted, or never present. Any other HMC failure still raises. Null is what
    one poll saw, not a confirmed disappearance: this tool polls once and never
    re-reads to confirm (a 404 against a supplied ``job_href`` is second-sourced
    against the global jobs path, but that is one poll, not a confirmation over
    time), so a momentary 404 from a proxy reload or a failover reads as null
    too, and a null that repeats for every identifier is a deployment whose jobs
    path is
    absent rather than a fleet of vanished jobs (ADR 0093). Absence can also be
    confined to one job: on firmware that does not serve the global jobs path, a
    ``job_href`` that has stopped resolving reads as null for that identifier
    while the job is alive and still pollable at the right link. Whenever you
    supplied a ``job_href``, re-read by ``job_uuid`` alone before acting on a
    null.

    An empty identifier, a bare dot, or one carrying a path, query, fragment,
    percent, or interior whitespace character addresses something other than one
    job and is rejected outright rather than reported as a missing job;
    surrounding whitespace is trimmed. That check runs even when ``job_href`` is
    supplied, so an empty ``job_uuid`` no longer passes through on the strength of
    the link alone.

    A ``job_href`` that stops resolving is re-read against the global jobs path
    before the job is called gone, so the ``link`` in the returned mapping can be
    the link that failed. That retirement is visible only in the server log, so a
    caller that passed a ``job_href`` and got a job back cannot tell from the
    result whether its link is still good; poll by ``job_uuid`` alone on any
    stale-link suspicion (#529). A supplied link also decides **which job is
    read** — the path is fetched directly and checked only for addressing a job
    resource — so a mispaired handle returns the *other* job. Compare the
    returned entry's UUID or JobID against the identifier you passed.

    Args:
        job_uuid: UUID or JobID returned when the job was submitted.
        job_href: Optional submission SELF link for firmware that cannot resolve the UUID.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def operation():
        async with client_from_env(profile) as hmc:
            outcome = await operations_jobs.get_job(
                hmc, job_uuid, job_href=job_href
            )
            return outcome.job

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

    Read ``found`` first, before ``timed_out``. A job the HMC no longer has —
    reaped, deleted, or never present — returns ``found`` false with a null
    ``status``, rather than raising. It also returns ``timed_out`` true, because
    no terminal status was observed, and it does so immediately rather than after
    ``timeout_seconds``: on a ``found`` false outcome ``timed_out`` carries no
    information and must not be read as "still running". Only
    ``found`` true with ``timed_out`` true means the HMC still has the job and it
    has not reached a terminal status, and a null ``status`` there means the entry
    carried no readable Status rather than that the job is running. Polling stops
    as soon as the job is gone instead of burning the remaining
    ``timeout_seconds``. Every other HMC failure still raises ``HMCError``.

    ``found`` false is what the read that was made saw. It is a *confirmed*
    disappearance only once this wait has already seen the job alive, in which
    case a second read has to agree. A first poll that produces no entry — a 404,
    or a response the HMC answers with no job entry at all — is reported straight
    through with no re-read, so one momentary or degraded response at the start of
    a wait ends it immediately.
    ``found`` false does not say *why*, and is not proof the work did or did not
    happen: confirm that against the affected resource, not the job record. A
    ``found`` false that repeats for every identifier is a deployment whose jobs
    path is absent, not a fleet of vanished jobs (ADR 0093). Absence can also be
    confined to one job: on firmware that does not serve the global jobs path, a
    ``job_href`` that has stopped resolving reads as ``found`` false for that
    identifier while the job is alive and still pollable at the right link.
    Whenever you supplied a ``job_href``, re-read by ``job_uuid`` alone before
    acting on absence.

    ``timeout_seconds`` is a soft bound. A job that disappears after this wait has
    already seen it alive is re-read once, up to one ``poll_interval`` later,
    before being reported gone, and that confirming read is owed even past the
    deadline. The overshoot is a whole ``poll_interval``, unrelated to the
    deadline, so a ``poll_interval`` larger than ``timeout_seconds`` overshoots by
    more than the deadline itself. Keep ``poll_interval`` well under
    ``timeout_seconds``. The gap shrinks the other way near the deadline: a
    disappearance seen with less than an interval left is confirmed after only the
    time that remains, which weakens the confirmation there (#532).

    ``job_href`` on the result is usually the link worth persisting for the next
    call: the link you passed when the read through it worked, otherwise the
    successful read's own link, and null when nothing resolved. A link that stopped
    resolving is dropped only when the HMC's own SELF link is spelled the same way,
    so one you stored in another spelling — a relative path against an absolute
    href — can come back unchanged after it has already failed (#529). The in-band
    signal is narrow but real: a link you passed that still resolves is always
    echoed back, so if you passed one and get a ``found`` true outcome whose
    ``job_href`` is null, that link was retired. Drop it and poll by ``job_uuid``
    alone, which is also the reliable recovery whenever a stored link is suspect.
    An echoed link is the exact string you passed, and only its path is ever
    requested. What was validated is not quite what comes back: host, query and
    fragment are neither checked nor normalized, and a tab, carriage return or
    newline is deleted while the path is built but survives in the echoed string
    (#537).
    It is your own input coming back, not something the HMC attested — do not
    dereference it as one.

    A supplied ``job_href`` also decides **which job is read**: the path is
    fetched directly and checked only for addressing a job resource, so a
    mispaired handle reads the *other* job. Compare the returned ``job_id``
    against the identifier you passed before acting on the result.

    An empty identifier, a bare dot, or one carrying a path, query, fragment,
    percent, or interior whitespace character addresses something other than one
    job and is rejected outright rather than reported as ``found`` false;
    surrounding whitespace is trimmed. That check runs even when ``job_href`` is
    supplied.

    The same two fields appear on the outcomes returned by the submit-and-wait
    tools (the migrate, remote-restart and power tools), where they describe a
    *submission* — there ``found`` false means "this submission returned no job
    entry", not "the HMC no longer has this job".

    Args:
        job_uuid: UUID or JobID returned when the job was submitted.
        timeout_seconds: Maximum polling duration in seconds; zero performs one poll.
        poll_interval: Seconds between polls; must be greater than zero.
        job_href: Optional submission SELF link for firmware that cannot resolve the UUID.
        profile: Optional configured HMC profile name; uses the default when omitted.
    """

    async def operation():
        async with client_from_env(profile) as hmc:
            return await operations_jobs.wait_for_job(
                hmc,
                job_uuid,
                job_href=job_href,
                timeout_seconds=timeout_seconds,
                poll_interval=poll_interval,
            )

    return _run(operation)
