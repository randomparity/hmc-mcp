"""Presentation-neutral managed-system operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hmcpctl.client.core import HMCClient

from ...documents.system import (
    MemoryMirroringMode,
    PowerOffPolicy,
    PowerOnLparStartPolicy,
    build_managed_system_document,
)
from ...jobs import (
    DEFAULT_JOB_POLL_INTERVAL,
    DEFAULT_JOB_TIMEOUT_SECONDS,
    validate_wait_timing,
    wait_for_submitted_job,
)
from ...resource_identity import resolve_system_uuid, summary_matching
from .fleet import system_entry


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
    """List managed systems, keeping only those whose ``State`` is *state*.

    The systems come from ``ManagedSystem/quick/All``, filtered locally: the full
    ``ManagedSystem`` feed and the ``State`` search time out while a system has
    no connection (#1501). An operating system's entry is its full
    ``ManagedSystem/{uuid}`` entry; any other system's is sparse (ADR 0197).

    Raises:
        HMCError: If the HMC request fails.
    """
    entries = []
    for summary in await hmc.list_system_summaries():
        if state is not None and (
            not isinstance(summary, dict) or summary.get("State") != state
        ):
            continue
        entries.append(await system_entry(hmc, summary))
    return entries


async def get_system(hmc: HMCClient, system_name_or_uuid: str) -> dict[str, Any] | None:
    """Return a managed system selected by exact name or UUID.

    The full entry for an operating system, a sparse one otherwise (ADR 0197).

    Raises:
        HMCError: If the HMC request fails.
        ValueError: If the name matches more than one managed system.
    """
    summaries = await hmc.list_system_summaries()
    summary = summary_matching(summaries, system_name_or_uuid)
    return await system_entry(hmc, summary) if summary is not None else None


async def modify_system(
    hmc: HMCClient,
    system_name_or_uuid: str,
    patch: ManagedSystemPatch,
) -> dict[str, Any] | None:
    """Apply the supplied configuration fields to a managed system.

    Raises:
        ResourceNotFoundError: If the managed-system selector cannot be resolved.
        ValueError: If the requested patch values are invalid.
        HMCError: If the HMC rejects the update or the request cannot be completed.
    """
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
    """Resolve a system selector, submit its power job, and optionally wait.

    Raises:
        ValueError: If wait timing is invalid.
        ResourceNotFoundError: If the managed-system selector cannot be resolved.
        HMCError: If job submission, polling, or the HMC request fails.
    """
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    if power_on:
        job = await hmc.power_on_system(system_uuid)
    else:
        job = await hmc.power_off_system(system_uuid, immediate=immediate)
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)
