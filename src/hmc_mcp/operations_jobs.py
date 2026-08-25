"""Presentation-neutral polling of an HMC job from a persisted identifier.

ADR 0093: the supported handle for a job is two plain strings — ``job_id`` and an
optional ``job_href``. A consumer can store them, restart, construct a fresh
``HMCClient``, and poll from a different process than the one that submitted the
work. Both operations are ordinary coroutines, so an in-process consumer awaits
them from inside its own running event loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from .client import HMCClient
from .errors import HMCError
from .jobs import (
    TERMINAL_JOB_STATUSES,
    JobOutcome,
    job_outcome,
    validate_wait_timing,
)

_logger = logging.getLogger(__name__)

#: The one HTTP status that means "this HMC does not have that job" rather than
#: "this request failed". Every other status stays an ``HMCError``.
_JOB_MISSING_STATUS = 404

#: Characters that would make an identifier address something other than one job
#: under ``/rest/api/uom/jobs/{id}``. No HMC-minted UUID or JobID contains one.
_ILLEGAL_JOB_ID_CHARACTERS = frozenset("/?#%")


def _require_job_id(job_id: str) -> str:
    """Return the trimmed identifier, rejecting one that addresses no job.

    A handle read back from storage can arrive truncated, mangled, or from the
    wrong column. Left unchecked, a value carrying a path or query separator
    builds a different request path, and the HMC's 404 would then be reported as
    the load-bearing ``found=False`` — telling a worker its job is gone when the
    request never addressed the job at all. Fail at the boundary instead.
    """
    identifier = job_id.strip() if isinstance(job_id, str) else ""
    if not identifier:
        raise ValueError("job_id must be a non-empty HMC job identifier")
    if any(
        character in _ILLEGAL_JOB_ID_CHARACTERS or character.isspace()
        for character in identifier
    ):
        raise ValueError(
            f"job_id {job_id!r} is not an HMC job identifier: it contains "
            "a path, query, or whitespace character. Store the UUID or JobID "
            "on its own; pass a submission link as job_href."
        )
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
    ``HMCError``. A ``job_id`` that could address something other than one job is
    rejected with ``ValueError`` rather than reported as a missing job.

    The returned ``job_href`` is the link the caller passed, when it passed one:
    that link demonstrably resolved, and rotating a stored handle to a SELF link
    from the response would risk replacing a working link with an untried one on
    exactly the firmware ``job_href`` exists to serve. Only when the caller
    supplied none does the response's SELF link become the handle.
    """
    identifier = _require_job_id(job_id)
    link = _clean_job_href(job_href)
    try:
        job = await hmc.get_job(identifier, job_href=link)
    except HMCError as exc:
        if exc.status_code != _JOB_MISSING_STATUS:
            raise
        _logger.info(
            "HMC job %s not found (HTTP %s%s): reporting found=False. Detail: %s",
            identifier,
            _JOB_MISSING_STATUS,
            " via job_href" if link else " via the global jobs path",
            exc,
        )
        job = None
    outcome = job_outcome(identifier, job)
    if link is not None and outcome.job_href != link:
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
