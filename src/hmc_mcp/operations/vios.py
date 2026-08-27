"""Presentation-neutral VIOS operations."""

from __future__ import annotations

from typing import Any

from ..client import HMCClient
from ..documents import LparResources, build_vios_document
from ..errors import HMCError
from ..resource_identity import resolve_system_uuid, resolve_vios_uuid
from ..jobs import (
    DEFAULT_JOB_POLL_INTERVAL,
    DEFAULT_JOB_TIMEOUT_SECONDS,
    validate_wait_timing,
    wait_for_submitted_job,
)


async def _create_vios(
    hmc: HMCClient,
    system_name_or_uuid: str,
    name: str,
    resources: LparResources,
) -> dict[str, Any] | None:
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    return await hmc.create_logical_partition(
        system_uuid, build_vios_document(name=name, resources=resources)
    )


async def _delete_vios(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    system_name_or_uuid: str | None = None,
) -> str:
    vios_uuid = await resolve_vios_uuid(
        hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    state = await hmc.get_quick_property(
        "LogicalPartition", vios_uuid, "PartitionState"
    )
    if state != "not activated":
        raise HMCError(
            f"Cannot delete VIOS {vios_uuid} — current state is {state!r}; it "
            "must be 'not activated' to delete. Power it off "
            "(hmc_power_off_vios) and confirm with hmc_get_lpar_state before retrying.",
            status_code=409,
        )
    await hmc.delete_logical_partition(vios_uuid)
    return f"Deleted VIOS {vios_uuid}"


async def power_vios(
    hmc: HMCClient,
    vios_name_or_uuid: str,
    *,
    on: bool,
    system_name_or_uuid: str | None = None,
    immediate: bool = False,
    wait: bool = False,
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    poll_interval: int = DEFAULT_JOB_POLL_INTERVAL,
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
