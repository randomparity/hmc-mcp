"""Presentation-neutral Live Partition Mobility operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .client import HMCClient
from .common import resolve_lpar_uuid, resolve_system_name
from .jobs import (
    JobOutcome,
    SUCCESSFUL_JOB_STATUSES,
    job_identifier,
    job_outcome,
    validate_wait_timing,
    wait_for_submitted_job,
)
from .errors import HMCError


@dataclass(frozen=True)
class LpmResult:
    """An LPM submission paired with its resolved partition identity."""

    lpar_uuid: str
    job: dict[str, Any] | JobOutcome | None


async def _finish_job(
    hmc: HMCClient,
    job: dict[str, Any] | None,
    wait: bool,
    timeout_seconds: int,
    poll_interval: int,
) -> JobOutcome:
    """Normalize an immediate submission or its final waited state."""
    submitted_id = job_identifier(job) if job is not None else None
    if not wait:
        return replace(job_outcome(submitted_id or "", job), timed_out=False)
    completed_job = await wait_for_submitted_job(
        hmc, job, True, timeout_seconds, poll_interval
    )
    return job_outcome(submitted_id or "", completed_job)


async def migrate_lpar(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    target_system_name_or_uuid: str,
    target_profile_name: str | None = None,
    wait_time: int | None = None,
    *,
    validate: bool = False,
    validate_first: bool = True,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> LpmResult:
    """Resolve selectors and submit standalone validation or validation-first migration."""
    effective_wait = wait or (validate_first and not validate)
    validate_wait_timing(effective_wait, timeout_seconds, poll_interval)
    lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
    target_system = await resolve_system_name(hmc, target_system_name_or_uuid)
    if validate:
        job = await hmc.lpar_migrate_validate(
            lpar_uuid, target_system, target_profile_name, wait_time=wait_time
        )
        return LpmResult(
            lpar_uuid,
            await _finish_job(hmc, job, wait, timeout_seconds, poll_interval),
        )
    if validate_first:
        validation_job = await hmc.lpar_migrate_validate(
            lpar_uuid, target_system, target_profile_name, wait_time=wait_time
        )
        validation = await _finish_job(
            hmc, validation_job, True, timeout_seconds, poll_interval
        )
        if validation.timed_out or validation.status not in SUCCESSFUL_JOB_STATUSES:
            detail = validation.error or "no validation error detail returned"
            raise HMCError(
                "LPM validation did not succeed "
                f"(status={validation.status or 'unknown'}, error={detail}); "
                "migration was not submitted"
            )
    job = await hmc.lpar_migrate(
        lpar_uuid, target_system, target_profile_name, wait_time=wait_time
    )
    return LpmResult(
        lpar_uuid,
        await _finish_job(hmc, job, wait, timeout_seconds, poll_interval),
    )


async def abort_lpar_migration(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    *,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> LpmResult:
    """Resolve and abort an in-progress migration."""
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
    job = await hmc.lpar_migrate_abort(lpar_uuid)
    return LpmResult(
        lpar_uuid,
        await _finish_job(hmc, job, wait, timeout_seconds, poll_interval),
    )


async def recover_lpar_migration(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    *,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> LpmResult:
    """Resolve and recover a failed migration."""
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
    job = await hmc.lpar_migrate_recover(lpar_uuid)
    return LpmResult(
        lpar_uuid,
        await _finish_job(hmc, job, wait, timeout_seconds, poll_interval),
    )


async def remote_restart_lpar(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    target_system_name_or_uuid: str,
    *,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> LpmResult:
    """Resolve both selectors and remotely restart a failed partition."""
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
    target_system = await resolve_system_name(hmc, target_system_name_or_uuid)
    job = await hmc.lpar_remote_restart(lpar_uuid, target_system)
    return LpmResult(
        lpar_uuid,
        await _finish_job(hmc, job, wait, timeout_seconds, poll_interval),
    )
