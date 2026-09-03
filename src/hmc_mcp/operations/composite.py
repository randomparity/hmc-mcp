"""Presentation-neutral composite inventory operations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from hmc_mcp.client.core import HMCClient

from ..resource_identity import resolve_lpar_uuid, resolve_system_uuid
from .capacity import lpar_processing_units


@dataclass(frozen=True)
class LparSummary:
    uuid: object | None
    name: object | None
    state: object | None
    rmc_state: object | None
    partition_type: object | None
    partition_id: object | None
    current_memory_mib: object | None
    desired_memory_mib: object | None
    current_proc_units: object | None
    desired_proc_units: object | None
    desired_vcpus: object | None
    dedicated_procs: object | None
    os_version: object | None
    os_type: object | None
    client_network_adapter_count: int
    description: object | None
    mapped_storage: None


@dataclass(frozen=True)
class SystemSummary:
    uuid: object | None
    name: object | None
    state: object | None
    mtms: object | None
    firmware_version: object | None
    total_memory_mib: int
    free_memory_mib: int
    total_proc_units: float
    free_proc_units: float
    lpar_count: int
    lpar_states: dict[str, int]
    vios_count: int


def _current_or_desired(resource: dict[str, Any], current: str, desired: str) -> Any:
    value = resource.get(current)
    return resource.get(desired) if value is None else value


def _lpar_summary(
    lpar: dict[str, Any],
    adapters: list[dict[str, Any]],
) -> LparSummary:
    """Build a summary dict from raw LPAR entry + adapter list."""
    res = lpar.get("Resource") or {}
    return LparSummary(
        uuid=lpar.get("UUID"),
        name=res.get("PartitionName"),
        state=res.get("PartitionState"),
        rmc_state=res.get("ResourceMonitoringControlState") or res.get("RMCState"),
        partition_type=res.get("PartitionType"),
        partition_id=res.get("PartitionID"),
        current_memory_mib=_current_or_desired(res, "CurrentMemory", "DesiredMemory"),
        desired_memory_mib=res.get("DesiredMemory"),
        # Current CPU: shared-processor units or dedicated CPUs
        current_proc_units=_current_or_desired(
            res, "CurrentProcessingUnits", "DesiredProcessingUnits"
        ),
        desired_proc_units=res.get("DesiredProcessingUnits"),
        desired_vcpus=res.get("DesiredVirtualProcessors"),
        dedicated_procs=res.get("DedicatedProcessors"),
        os_version=res.get("OperatingSystemVersion"),
        os_type=res.get("OperatingSystemType"),
        client_network_adapter_count=len(adapters),
        description=res.get("Description"),
        # Note: mapped vSCSI storage requires VIOS UUID resolution
        # (vSCSI adapter → vios_partition_id → VIOS UUID → mapping groups
        #  filtered by LPAR link) and is not included here. List VIOS resources to
        #  retrieve per-VIOS storage mappings, then filter by the LPAR's
        #  partition ID.
        mapped_storage=None,
    )


async def lpar_summary(
    hmc: HMCClient,
    system_name_or_uuid: str | None,
    lpar_name_or_uuid: str,
) -> LparSummary:
    """Compose partition details and adapter inventory into one summary.

    Raises ``ValueError`` when the partition cannot be found. ``mapped_storage``
    remains unset because resolving it requires a separate VIOS inventory hop.
    """
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    lpar, adapters = await _fetch_lpar_data(hmc, lpar_uuid)
    return _lpar_summary(lpar, adapters)


async def _fetch_lpar_data(
    hmc, lpar_uuid: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch LPAR entry and its client network adapters concurrently."""
    async with asyncio.TaskGroup() as tasks:
        lpar_task = tasks.create_task(hmc.get_logical_partition(lpar_uuid))
        adapters_task = tasks.create_task(
            hmc.list_child("LogicalPartition", lpar_uuid, "ClientNetworkAdapter")
        )
    lpar = lpar_task.result()
    adapters = adapters_task.result()
    if lpar is None:
        raise ValueError(
            f"LPAR {lpar_uuid!r} not found after resolution. "
            "List logical partitions to inspect the available partitions."
        )
    return lpar, adapters


async def _fetch_system_summary_data(
    hmc,
    system_uuid: str,
) -> tuple[dict, list[dict], list[dict]]:
    """Fetch system entry, LPARs, and VIOS concurrently."""
    async with asyncio.TaskGroup() as tasks:
        system_task = tasks.create_task(hmc.get_managed_system(system_uuid))
        lpars_task = tasks.create_task(hmc.list_logical_partitions(system_uuid))
        vios_task = tasks.create_task(hmc.list_vios(system_uuid))
    system = system_task.result()
    lpars = lpars_task.result()
    vios_list = vios_task.result()
    if system is None:
        raise ValueError(
            f"Managed system {system_uuid!r} not found after resolution. "
            "List managed systems to inspect the available systems."
        )
    return system, lpars, vios_list


def _system_summary(
    system: dict[str, Any],
    lpars: list[dict[str, Any]],
    vios_list: list[dict[str, Any]],
) -> SystemSummary:
    """Build a summary dict from raw system entry, LPAR list, and VIOS list."""
    res = system.get("Resource") or {}

    lpar_states: dict[str, int] = {}
    for lpar in lpars:
        lr = lpar.get("Resource") or {}
        state = lr.get("PartitionState") or "unknown"
        lpar_states[state] = lpar_states.get(state, 0) + 1

    total_mem = int(res.get("AssignableSystemMemory") or 0)
    total_procs = float(res.get("ConfigurableSystemProcessorUnits") or 0.0)
    assigned_mem = sum(
        int((lpar.get("Resource") or {}).get("DesiredMemory") or 0) for lpar in lpars
    )
    assigned_procs = sum(lpar_processing_units(lpar) for lpar in lpars)

    return SystemSummary(
        uuid=system.get("UUID"),
        name=res.get("SystemName"),
        state=res.get("State"),
        mtms=res.get("MachineTypeModelSerialNumber"),
        firmware_version=res.get("SystemFirmware") or res.get("FirmwareVersion"),
        total_memory_mib=total_mem,
        free_memory_mib=total_mem - assigned_mem,
        total_proc_units=total_procs,
        free_proc_units=round(total_procs - assigned_procs, 4),
        lpar_count=len(lpars),
        lpar_states=lpar_states,
        vios_count=len(vios_list),
    )


async def system_summary(hmc: HMCClient, system_name_or_uuid: str) -> SystemSummary:
    """Compose system, partition, and VIOS inventory into one summary.

    Raises ``ValueError`` when the managed system cannot be found.
    """
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    system, lpars, vios_list = await _fetch_system_summary_data(hmc, system_uuid)
    return _system_summary(system, lpars, vios_list)
