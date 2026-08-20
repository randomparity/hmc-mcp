"""Presentation-neutral managed-system operations."""

from __future__ import annotations

from typing import Any

from .client import HMCClient
from .common import resolve_system_uuid
from .jobs import validate_wait_timing, wait_for_submitted_job


async def power_system(
    hmc: HMCClient,
    system_name_or_uuid: str,
    *,
    on: bool,
    immediate: bool = False,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any] | None:
    """Resolve a system selector, submit its power job, and optionally wait."""
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    if on:
        job = await hmc.power_on_system(system_uuid)
    else:
        job = await hmc.power_off_system(system_uuid, immediate=immediate)
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)
