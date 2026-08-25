"""Presentation-neutral polling of an HMC job from a persisted identifier.

ADR 0093: the supported handle for a job is two plain strings — ``job_id`` and an
optional ``job_href``. A consumer can store them, restart, construct a fresh
``HMCClient``, and poll from a different process than the one that submitted the
work. Both operations are ordinary coroutines, so an in-process consumer awaits
them from inside its own running event loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from .client import HMCClient
from .errors import HMCError
from .jobs import (
    TERMINAL_JOB_STATUSES,
    JobOutcome,
    job_outcome,
    validate_wait_timing,
)

#: The one HTTP status that means "this HMC does not have that job" rather than
#: "this request failed". Every other status stays an ``HMCError``.
_JOB_MISSING_STATUS = 404


def _require_job_id(job_id: str) -> str:
    """Return the trimmed identifier, rejecting one that addresses no job."""
    identifier = job_id.strip() if isinstance(job_id, str) else ""
    if not identifier:
        raise ValueError("job_id must be a non-empty HMC job identifier")
    return identifier


def _clean_job_href(job_href: str | None) -> str | None:
    """Treat a blank link as absent so the returned handle stays truthful.

    ``HMCClient.get_job`` already falls back to the global jobs path for a blank
    href, so echoing one back as if it were a usable link would be a lie.
    """
    return job_href.strip() if job_href and job_href.strip() else None


async def get_job(
    hmc: HMCClient,
    job_id: str,
    *,
    job_href: str | None = None,
) -> JobOutcome:
    """Read one HMC job by persisted identifier and normalize its outcome.

    *job_id* is the UUID or JobID the HMC minted when the job was submitted;
    *job_href* is that submission's SELF link, needed only on firmware that cannot
    resolve the identifier through the documented global jobs path (issue #95).
    Neither argument requires anything held in memory since submission.

    A job the HMC no longer knows about — reaped, deleted, or never present —
    returns ``found=False`` rather than raising, so a restarted worker can tell it
    apart from a job that is still running. Every other HMC failure still raises
    ``HMCError``.
    """
    identifier = _require_job_id(job_id)
    link = _clean_job_href(job_href)
    try:
        job = await hmc.get_job(identifier, job_href=link)
    except HMCError as exc:
        if exc.status_code != _JOB_MISSING_STATUS:
            raise
        job = None
    outcome = job_outcome(identifier, job)
    if outcome.job_href is None and link is not None:
        return replace(outcome, job_href=link)
    return outcome


async def wait_for_job(
    hmc: HMCClient,
    job_id: str,
    *,
    job_href: str | None = None,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> JobOutcome:
    """Poll a persisted job identifier until it settles, vanishes, or times out.

    Polling stops at the first of: a terminal status (``timed_out=False``, with
    ``status`` and ``error`` classified by ADR 0081), a job the HMC no longer
    knows about (``found=False``), or the deadline (``found=True`` and
    ``timed_out=True``, carrying the last observed status).

    ``timeout_seconds=0`` performs exactly one poll. Cancelling the returned
    coroutine is safe: it never logs the injected client on or off and issues no
    writes, so cancellation leaves no session state to unwind and does not disturb
    the HMC-side job.
    """
    identifier = _require_job_id(job_id)
    validate_wait_timing(True, timeout_seconds, poll_interval)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        outcome = await get_job(hmc, identifier, job_href=job_href)
        if not outcome.found or outcome.status in TERMINAL_JOB_STATUSES:
            return outcome
        remaining = deadline - loop.time()
        if remaining <= 0:
            return outcome
        await asyncio.sleep(min(poll_interval, remaining))
