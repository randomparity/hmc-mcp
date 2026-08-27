"""Presentation-neutral VIOS operations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..client import HMCClient
from ..config import HMCConfig
from ..documents import LparResources, build_vios_document
from ..errors import HMCError
from ..resource_identity import is_uuid, resolve_system_uuid, resolve_vios_uuid
from ..ssh import run_hmc_cli
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
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)


async def _resolve_vios_backup_system_name(
    hmc: HMCClient, system_name_or_uuid: str
) -> str:
    """Resolve a system UUID to its unique CLI MTMS identity."""
    if not is_uuid(system_name_or_uuid):
        return system_name_or_uuid
    entry = await hmc.get_managed_system(system_name_or_uuid)
    resource = (entry or {}).get("Resource") or {}
    mtms = resource.get("MachineTypeModelSerialNumber")
    if isinstance(mtms, str):
        machine_type, dash, model_and_serial = mtms.partition("-")
        model, star, serial = model_and_serial.partition("*")
        components = (machine_type, model, serial)
        if dash and star and all(part and part == part.strip() for part in components):
            rendered = f"{machine_type}-{model}*{serial}"
            if rendered == mtms:
                return rendered
    elif isinstance(mtms, Mapping):
        components = (
            mtms.get("MachineType"),
            mtms.get("Model"),
            mtms.get("SerialNumber"),
        )
        if all(isinstance(part, str) and part.strip() for part in components):
            machine_type, model, serial = components
            return f"{machine_type}-{model}*{serial}"
    raise ValueError(
        f"Managed system {system_name_or_uuid!r} has no complete, valid "
        "MachineTypeModelSerialNumber (MTMS). Use hmc_list_systems to inspect "
        "the managed system before retrying."
    )


async def _run_vios_backup_mutation(
    config: HMCConfig,
    system_name_or_uuid: str,
    vios_name_or_uuid: str,
    build_command: Callable[[str, str], str],
) -> str:
    """Resolve backup selectors and run one VIOS catalog mutation command."""
    system_name = system_name_or_uuid
    vios_uuid = vios_name_or_uuid
    if is_uuid(system_name_or_uuid) or not is_uuid(vios_name_or_uuid):
        async with HMCClient(config) as hmc:
            if is_uuid(system_name_or_uuid):
                system_name = await _resolve_vios_backup_system_name(
                    hmc, system_name_or_uuid
                )
            if not is_uuid(vios_name_or_uuid):
                vios_uuid = await resolve_vios_uuid(
                    hmc, vios_name_or_uuid, system_name_or_uuid=system_name_or_uuid
                )
    return await run_hmc_cli(build_command(system_name, vios_uuid), config)


async def _run_vios_backup_listing(
    config: HMCConfig,
    vios_name_or_uuid: str,
    build_command: Callable[[str], str],
) -> str:
    """Resolve a VIOS selector and run one catalog listing command."""
    vios_uuid = vios_name_or_uuid
    if not is_uuid(vios_name_or_uuid):
        async with HMCClient(config) as hmc:
            vios_uuid = await resolve_vios_uuid(hmc, vios_name_or_uuid)
    return await run_hmc_cli(build_command(vios_uuid), config)
