"""Presentation-neutral Live Partition Mobility operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import HMCClient
from .common import resolve_lpar_uuid, resolve_system_name
from .jobs import validate_wait_timing, wait_for_submitted_job


@dataclass(frozen=True)
class LpmResult:
    """An LPM submission paired with its resolved partition identity."""

    lpar_uuid: str
    job: dict[str, Any] | None


async def migrate_lpar(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    target_system_name_or_uuid: str,
    target_profile_name: str | None = None,
    wait_time: int | None = None,
    *,
    validate: bool = False,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> LpmResult:
    """Resolve selectors, submit migration or validation, and optionally wait."""
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
    target_system = await resolve_system_name(hmc, target_system_name_or_uuid)
    submit = hmc.lpar_migrate_validate if validate else hmc.lpar_migrate
    job = await submit(
        lpar_uuid, target_system, target_profile_name, wait_time=wait_time
    )
    completed_job = await wait_for_submitted_job(
        hmc, job, wait, timeout_seconds, poll_interval
    )
    return LpmResult(lpar_uuid, completed_job)


async def abort_lpar_migration(hmc: HMCClient, lpar_name_or_uuid: str) -> LpmResult:
    """Resolve and abort an in-progress migration."""
    lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
    return LpmResult(lpar_uuid, await hmc.lpar_migrate_abort(lpar_uuid))


async def recover_lpar_migration(hmc: HMCClient, lpar_name_or_uuid: str) -> LpmResult:
    """Resolve and recover a failed migration."""
    lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
    return LpmResult(lpar_uuid, await hmc.lpar_migrate_recover(lpar_uuid))


async def remote_restart_lpar(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    target_system_name_or_uuid: str,
) -> LpmResult:
    """Resolve both selectors and remotely restart a failed partition."""
    lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
    target_system = await resolve_system_name(hmc, target_system_name_or_uuid)
    job = await hmc.lpar_remote_restart(lpar_uuid, target_system)
    return LpmResult(lpar_uuid, job)
