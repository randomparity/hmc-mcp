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
from typing import Any

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
    illegal = any(
        character in _ILLEGAL_JOB_ID_CHARACTERS
        or character.isspace()
        or not character.isprintable()
        for character in identifier
    )
    if illegal or set(identifier) == {"."}:
        raise ValueError(
            f"job_id {job_id!r} is not an HMC job identifier: it is a path "
            "segment, or contains a path, query, whitespace, or non-printable "
            "character. Store the UUID or JobID on its own; pass a submission "
            "link as job_href."
        )
    return identifier


def _clean_job_href(job_href: str | None) -> str | None:
    """Treat a blank link as absent so the returned handle stays truthful.

    ``HMCClient.get_job`` already falls back to the global jobs path for a blank
    href, so echoing one back as if it were a usable link would be a lie.

    The link is not otherwise sanitized, which is why every warning below
    interpolates it with ``%r`` rather than ``%s``. ``urlsplit`` *deletes* tab,
    carriage return and newline while building the path, so the string
    ``client._reject_non_job_path`` validates is not this one: an embedded
    newline survives here having passed that check. These records reach the
    stderr stream ADR 0040 defines as one JSON record per line, and this value is
    caller-supplied on the ``hmc_get_job`` and ``hmc_wait_for_job`` tools, so a
    bare ``%s`` would let a caller write a forged record at column 0. ``%r``
    escapes it.
    """
    return job_href.strip() if job_href and job_href.strip() else None


def _says_the_path_has_no_job(exc: HMCError) -> bool:
    """Whether *exc* says this HMC has no job there, rather than that it failed.

    Two shapes qualify: the documented 404, and the HTTP 400 REST000E the client
    turns into "this endpoint is not available on this HMC" (issue #95). A 5xx, a
    connection reset, or a read timeout is a *degraded* HMC — ``HMCTransportError``
    subclasses ``HMCError``, so catching the base class would convert one into the
    load-bearing ``found=False``, which is the one wrong answer this path can give.
    """
    if exc.status_code == _JOB_MISSING_STATUS:
        return True
    return exc.status_code == 400 and "REST000E" in f"{exc}{exc.body or ''}"


async def _confirm_missing(
    hmc: HMCClient, identifier: str, link: str, missing: HMCError
) -> dict[str, Any] | None:
    """Second-source a 404 raised against a caller-supplied link.

    A per-operation SELF link embeds the target resource, not just the job
    (``.../LogicalPartition/{uuid}/do/PowerOn/Job/{id}``), so it can stop
    resolving while the job is fine — this package's own decommission operations
    remove such parents. Confirm against the global jobs path, which is keyed on
    the identifier the caller actually asked about, before reporting the job gone.

    The confirmation is best-effort about *absence* only: firmware that does not
    serve the global path leaves the original 404 standing rather than replacing a
    documented ``found=False`` with an exception. It is not best-effort about
    failure — a degraded HMC on this read propagates, exactly as it does on the
    primary read, because reporting a job gone on the strength of a socket reset
    is the answer a consumer acts on destructively.
    """
    try:
        job = await hmc.get_job(identifier, job_href=None)
    except HMCError as exc:
        if not _says_the_path_has_no_job(exc):
            raise
        job = None
    if job is not None:
        _logger.warning(
            "job_href %r no longer resolves for HMC job %s, but the global jobs "
            "path still has it. Re-store the handle without the stale link.",
            link,
            identifier,
        )
        return job
    _logger.warning(
        "HMC job %s not found via job_href %r, and the global jobs path did not "
        "have it either: reporting found=False. An HMC that does not serve the "
        "global jobs path produces the same answer. Detail: %s",
        identifier,
        link,
        missing,
    )
    return None


async def _read_job(
    hmc: HMCClient,
    identifier: str,
    link: str | None,
    dead_link: str | None = None,
) -> tuple[JobOutcome, bool]:
    """Perform one poll; also report whether *link* proved stale on this read.

    *dead_link* is a link an earlier read in the same wait already proved stale.
    It has to be carried, not just stopped being used: an HMC job entry's SELF
    link is the per-operation link, so a later read through the global path can
    advertise the dead link right back, and the outcome a consumer re-persists
    would then carry a link this package knows does not resolve.
    """
    reported = False
    stale_link = False
    try:
        job = await hmc.get_job(identifier, job_href=link)
    except HMCError as exc:
        if exc.status_code != _JOB_MISSING_STATUS:
            raise
        reported = True
        if link is None:
            _logger.warning(
                "HMC job %s not found via the global jobs path: reporting "
                "found=False. A deployment whose job path this HMC does not "
                "serve produces the same answer. Detail: %s",
                identifier,
                exc,
            )
            job = None
        else:
            job = await _confirm_missing(hmc, identifier, link, exc)
            stale_link = job is not None
    if job is None and not reported:
        _logger.warning(
            "HMC returned no entry for job %s %s: reporting found=False. An HMC "
            "answering the jobs path with no content gives the same answer for "
            "every identifier.",
            identifier,
            f"via job_href {link!r}" if link else "via the global jobs path",
        )
    outcome = job_outcome(identifier, job)
    # A link this read just retired is no longer supplied; it is dead.
    supplied = None if stale_link else link
    dead = link if stale_link else dead_link
    handle = _handle(outcome, job, supplied, dead)
    return replace(outcome, job_href=handle), stale_link


def _handle(
    outcome: JobOutcome,
    job: dict[str, Any] | None,
    link: str | None,
    dead_link: str | None,
) -> str | None:
    """Return the link worth persisting from this read, or ``None`` if none is.

    A link the caller supplied is kept only when the read through it produced the
    job. A link already known dead — the one supplied on a read that 404'd, or one
    an earlier read in this wait retired — is never handed back, so a consumer
    re-persisting from every outcome cannot store a link known not to work.
    """
    if job is None:
        return None
    if link is not None:
        return link
    return None if outcome.job_href == dead_link else outcome.job_href


def _warn_if_another_job_answered(
    identifier: str, outcome: JobOutcome, link: str | None
) -> bool:
    """Warn when a caller-supplied link produced a differently named job.

    Only a supplied link can substitute a job: without one the request path is
    built from the identifier, so a differing ``job_id`` there only means the
    response labelled the same job with its other identifier.

    Returns whether it warned, so a wait can fire this on first detection and
    then stay quiet for the rest of its polls.
    """
    if link is None or not outcome.found or outcome.job_id == identifier:
        return False
    _logger.warning(
        "HMC job_href %r returned job %s for requested identifier %s. The "
        "outcome describes the job that was read. This is expected when the "
        "stored handle is a JobID and the response carries a UUID; it is a "
        "mispaired handle otherwise.",
        link,
        outcome.job_id,
        identifier,
    )
    return True


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
    ``HMCError``. A ``job_id`` that is a path segment, or that could address
    something other than one job, is rejected with ``ValueError`` rather than
    reported as a missing job.

    A 404 against a supplied ``job_href`` is confirmed against the global jobs
    path before it becomes ``found=False``: a per-operation SELF link embeds the
    target resource, so it can stop resolving while the job is fine. When that
    second read finds the job, this returns it and warns that the stored link is
    stale.

    The returned ``job_href`` is the link the caller passed, when that link
    resolved: it demonstrably works, and rotating a stored handle to a SELF link
    from the response would risk replacing it with an untried one on exactly the
    firmware ``job_href`` exists to serve. Where the caller supplied no link, or
    supplied one the confirming read proved stale, the handle is the href the
    successful read carried — so a consumer that re-persists ``job_href`` from
    every outcome never stores a link known not to work.

    Re-persist ``job_href``, not ``job_id``. Never overwrite a stored ``job_id``
    from an outcome whose ``job_id`` differs from the identifier you asked about:
    that difference is the only mispairing signal there is, and writing over it
    makes a transient mismatch permanent and self-consistent.

    **A supplied ``job_href`` decides which job is read, not ``job_id``.** The
    client fetches that link's path directly and validates only that it addresses
    a job resource, so a mispaired handle — the two columns of one row written
    out of step — reads the *other* job. The returned ``job_id`` is
    response-derived, so it names the job actually read, and a difference from
    the requested identifier logs a warning rather than raising. Treat that
    comparison as advisory: ``jobs.job_identifier`` prefers the response's UUID
    over its JobID, so a handle stored as a JobID differs from the returned
    ``job_id`` on firmware that reports both, with no substitution involved.
    """
    identifier = _require_job_id(job_id)
    link = _clean_job_href(job_href)
    outcome, stale_link = await _read_job(hmc, identifier, link)
    _warn_if_another_job_answered(identifier, outcome, None if stale_link else link)
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

    **Any non-404 HMC failure aborts the wait as an ``HMCError``** — a 5xx, a
    network timeout, or an expired session, which matters because ``HMCClient``
    performs no re-logon and a wait sized to a multi-hour job can outlive the
    HMC's session lifetime. There is no retry inside this operation, deliberately:
    it is a pure read, and re-calling it with the same ``job_id`` and ``job_href``
    resumes exactly where it stopped. So size ``timeout_seconds`` to how long one
    session can be expected to last, and drive a longer wait by calling again.

    A job that disappears **after** the wait has seen it alive is not reported
    gone on one read. A single 404 is the only failure on this path that returns
    successfully instead of raising, so accepting it outright would let a
    momentary one — a proxy reload, a failover — be reported as a vanished job to
    a consumer the ADR expects to act on that destructively. The wait re-reads
    once, one poll interval later, and reports ``found=False`` only if the second
    read agrees. That confirming read is owed even when the disappearance lands on
    the last poll before the deadline, so a wait can run one poll interval past
    ``timeout_seconds`` rather than return an unconfirmed vanish. A job missing
    from the *first* read is reported immediately: there is no earlier observation
    to contradict it.

    The disappearance is returned as a bare ``found=False``: the outcome does not
    carry the status observed on the poll before, because ``found=False`` means
    the HMC produced no entry and inventing a last-known status on it would
    contradict that. The evidence is not lost — the transition is logged at
    warning, naming the last status seen — but a consumer that needs it must read
    the log or poll in its own loop.
    """
    identifier = _require_job_id(job_id)
    link = _clean_job_href(job_href)
    validate_wait_timing(True, timeout_seconds, poll_interval)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    last_status: str | None = None
    observed = False
    rereading = False
    warned = False
    dead_link: str | None = None
    while True:
        outcome, stale_link = await _read_job(hmc, identifier, link, dead_link)
        if stale_link:
            dead_link, link = link, None
        if not warned:
            warned = _warn_if_another_job_answered(identifier, outcome, link)
        if outcome.found:
            observed, rereading, last_status = True, False, outcome.status
            if outcome.status in TERMINAL_JOB_STATUSES:
                break
        elif not observed or rereading:
            break
        else:
            rereading = True
            _logger.warning(
                "HMC job %s disappeared during the wait; the last status observed "
                "was %s. Re-reading once before reporting it gone.",
                identifier,
                last_status,
            )
        remaining = deadline - loop.time()
        if remaining <= 0:
            if not rereading:
                break
            # The confirming read is owed, so it outlives the deadline by one
            # interval rather than shipping an unconfirmed "your job is gone".
            remaining = poll_interval
        await asyncio.sleep(min(poll_interval, remaining))
    return outcome
