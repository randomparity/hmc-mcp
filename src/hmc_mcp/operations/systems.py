"""Presentation-neutral managed-system operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hmc_mcp.client.core import HMCClient

from ..documents import (
    MemoryMirroringMode,
    PowerOffPolicy,
    PowerOnLparStartPolicy,
    build_managed_system_document,
)
from ..jobs import (
    DEFAULT_JOB_POLL_INTERVAL,
    DEFAULT_JOB_TIMEOUT_SECONDS,
    validate_wait_timing,
    wait_for_submitted_job,
)
from ..resource_identity import is_uuid, resolve_system_uuid


@dataclass(frozen=True)
class ManagedSystemPatch:
    new_name: str | None = None
    power_off_policy: PowerOffPolicy | None = None
    power_on_lpar_start_policy: PowerOnLparStartPolicy | None = None
    pend_mem_region_size: int | None = None
    requested_num_sys_huge_pages: int | None = None
    mem_mirroring_mode: MemoryMirroringMode | None = None


async def list_systems(
    hmc: HMCClient, state: str | None = None
) -> list[dict[str, Any]]:
    """List managed systems, using server-side state filtering when requested.

    Raises:
        HMCError: If the HMC request fails.
    """
    if state is not None:
        return await hmc.search_uom("ManagedSystem", "State", state)
    return await hmc.list_managed_systems()


async def get_system(hmc: HMCClient, system_name_or_uuid: str) -> dict[str, Any] | None:
    """Return a managed system selected by exact name or UUID.

    Raises:
        HMCError: If the HMC request fails.
    """
    if is_uuid(system_name_or_uuid):
        return await hmc.get_managed_system(system_name_or_uuid)
    return await hmc.find_system_by_name(system_name_or_uuid)


async def modify_system(
    hmc: HMCClient,
    system_name_or_uuid: str,
    patch: ManagedSystemPatch,
) -> dict[str, Any] | None:
    """Apply the supplied configuration fields to a managed system."""
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    document = build_managed_system_document(
        new_name=patch.new_name,
        power_off_policy=patch.power_off_policy,
        power_on_lpar_start_policy=patch.power_on_lpar_start_policy,
        pend_mem_region_size=patch.pend_mem_region_size,
        requested_num_sys_huge_pages=patch.requested_num_sys_huge_pages,
        mem_mirroring_mode=patch.mem_mirroring_mode,
    )
    return await hmc.modify_managed_system(system_uuid, document)


async def power_system(
    hmc: HMCClient,
    system_name_or_uuid: str,
    *,
    power_on: bool,
    immediate: bool = False,
    wait: bool = False,
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    poll_interval: int = DEFAULT_JOB_POLL_INTERVAL,
) -> dict[str, Any] | None:
    """Resolve a system selector, submit its power job, and optionally wait."""
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    if power_on:
        job = await hmc.power_on_system(system_uuid)
    else:
        job = await hmc.power_off_system(system_uuid, immediate=immediate)
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)
