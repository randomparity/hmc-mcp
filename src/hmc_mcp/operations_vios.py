"""Presentation-neutral VIOS operations."""

from __future__ import annotations

from typing import Any

from .client import HMCClient
from .common import resolve_vios_uuid
from .jobs import validate_wait_timing, wait_for_submitted_job


async def power_vios(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    *,
    on: bool,
    system_name_or_uuid: str | None = None,
    immediate: bool = False,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
) -> dict[str, Any] | None:
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    if on:
        job = await hmc.power_on_vios(vios_uuid)
    else:
        job = await hmc.power_off_vios(vios_uuid, immediate=immediate)
    return await wait_for_submitted_job(
        hmc, job, wait, timeout_seconds, poll_interval
    )
