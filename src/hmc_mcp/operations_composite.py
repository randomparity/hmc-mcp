"""Presentation-neutral composite inventory operations."""

from __future__ import annotations

import asyncio
from typing import Any

from .client import HMCClient
from .common import resolve_lpar_uuid, resolve_system_uuid
from .operations_capacity import lpar_processing_units


def _current_or_desired(resource: dict[str, Any], current: str, desired: str) -> Any:
    value = resource.get(current)
    return resource.get(desired) if value is None else value


def _lpar_summary(
    lpar: dict[str, Any],
    adapters: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a summary dict from raw LPAR entry + adapter list."""
    res = lpar.get("Resource") or {}
    return {
        "uuid": lpar.get("UUID"),
        "name": res.get("PartitionName"),
        "state": res.get("PartitionState"),
        "rmc_state": res.get("ResourceMonitoringControlState") or res.get("RMCState"),
        "partition_type": res.get("PartitionType"),
        "partition_id": res.get("PartitionID"),
        # Current memory/CPU (what the LPAR currently has)
        "current_memory_mb": _current_or_desired(
            res, "CurrentMemory", "DesiredMemory"
        ),
        "desired_memory_mb": res.get("DesiredMemory"),
        # Current CPU: shared-processor units or dedicated CPUs
        "current_proc_units": _current_or_desired(
            res, "CurrentProcessingUnits", "DesiredProcessingUnits"
        ),
        "desired_proc_units": res.get("DesiredProcessingUnits"),
        "desired_vcpus": res.get("DesiredVirtualProcessors"),
        "dedicated_procs": res.get("DedicatedProcessors"),
        # OS info
        "os_version": res.get("OperatingSystemVersion"),
        "os_type": res.get("OperatingSystemType"),
        # Adapters
        "client_network_adapter_count": len(adapters),
        # Description (REST field; full text from the resource if present)
        "description": res.get("Description"),
        # Note: mapped vSCSI storage requires VIOS UUID resolution
        # (vSCSI adapter → vios_partition_id → VIOS UUID → ViosStorageDetail
        #  filtered by LPAR link) and is not included here. Use hmc_list_vios() to
        #  retrieve per-VIOS storage mappings, then filter by the LPAR's
        #  partition ID.
        "mapped_storage": None,
    }


async def lpar_summary(hmc: HMCClient, lpar_name_or_uuid: str) -> dict[str, Any]:
    """One-call LPAR summary: state, RMC, memory/CPU, OS, adapter count, description.

    Composes data from three HMC endpoints in a single call:

    1. ``GET /rest/api/uom/LogicalPartition/{uuid}`` — main partition data
       (state, memory, CPU, OS version, partition type / ID).
    2. ``GET /rest/api/uom/LogicalPartition/{uuid}/ClientNetworkAdapter`` —
       client network adapters attached to the partition (count returned).

    Accepts either a PartitionName (exact match) or a UUID.

    Returns a flat summary dict with the most useful fields:

    - ``state`` / ``rmc_state`` — current PartitionState and RMC status.
    - ``current_memory_mb`` / ``desired_memory_mb`` — active and profile memory.
    - ``current_proc_units`` / ``desired_proc_units`` — active and profile CPU.
    - ``desired_vcpus`` / ``dedicated_procs`` — virtual processors or dedicated CPUs.
    - ``os_version`` / ``os_type`` — OS details reported by the HMC.
    - ``client_network_adapter_count`` — number of client network adapters.
    - ``description`` — partition description if set.
    - ``mapped_storage`` — always ``null``; resolving vSCSI-mapped storage
      requires a VIOS UUID hop (vSCSI adapter → ``vios_partition_id`` →
      ``list_vios`` + PartitionID match → ``get_vios_storage_detail``) and is
      out of scope for this best-effort summary. Use ``hmc_list_vios`` for per-VIOS
      storage mappings.

    Raises ``ValueError`` when the partition cannot be found.
    """
    lpar_uuid = await resolve_lpar_uuid(hmc, lpar_name_or_uuid)
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
            "Use hmc_list_lpars to list available partitions."
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
            "Use hmc_list_systems to list available systems."
        )
    return system, lpars, vios_list


def _system_summary(
    system: dict[str, Any],
    lpars: list[dict[str, Any]],
    vios_list: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a summary dict from raw system entry, LPAR list, and VIOS list."""
    res = system.get("Resource") or {}

    # LPAR counts by state
    lpar_states: dict[str, int] = {}
    for lpar in lpars:
        lr = lpar.get("Resource") or {}
        state = lr.get("PartitionState") or "unknown"
        lpar_states[state] = lpar_states.get(state, 0) + 1

    # Free resources
    total_mem = int(res.get("AssignableSystemMemory") or 0)
    total_procs = float(res.get("ConfigurableSystemProcessorUnits") or 0.0)
    assigned_mem = sum(
        int((lpar.get("Resource") or {}).get("DesiredMemory") or 0) for lpar in lpars
    )
    assigned_procs = sum(lpar_processing_units(lpar) for lpar in lpars)

    return {
        "uuid": system.get("UUID"),
        "name": res.get("SystemName"),
        "state": res.get("State"),
        "mtms": res.get("MachineTypeModelSerialNumber"),
        "firmware_version": res.get("SystemFirmware") or res.get("FirmwareVersion"),
        "total_memory_mb": total_mem,
        "free_memory_mb": total_mem - assigned_mem,
        "total_proc_units": total_procs,
        "free_proc_units": round(total_procs - assigned_procs, 4),
        "lpar_count": len(lpars),
        "lpar_states": lpar_states,
        "vios_count": len(vios_list),
    }


async def system_summary(hmc: HMCClient, system_name_or_uuid: str) -> dict[str, Any]:
    """One-call managed system summary: state, MTMS, firmware, LPAR counts, free resources, VIOS count.

    Composes data from three HMC endpoints in a single call:

    1. ``GET /rest/api/uom/ManagedSystem/{uuid}`` — main system data
       (state, MTMS, firmware version, total memory/CPU).
    2. ``GET /rest/api/uom/ManagedSystem/{uuid}/LogicalPartition`` — LPAR list,
       counted by PartitionState.
    3. ``GET /rest/api/uom/ManagedSystem/{uuid}/VirtualIOServer`` — VIOS count.

    Accepts either a SystemName (exact match) or a UUID.

    Returns a flat summary dict with the most useful fields:

    - ``state`` — current system State (operating, standby, ...).
    - ``mtms`` — machine type/model/serial (MachineTypeModelSerialNumber).
    - ``firmware_version`` — system firmware version string.
    - ``total_memory_mb`` / ``free_memory_mb`` — total assignable and free memory.
    - ``total_proc_units`` / ``free_proc_units`` — total configurable and free CPU.
    - ``lpar_count`` — total number of LPARs.
    - ``lpar_states`` — dict mapping PartitionState → count.
    - ``vios_count`` — number of Virtual I/O Servers.

    Raises ``ValueError`` when the system cannot be found.
    """
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    system, lpars, vios_list = await _fetch_system_summary_data(hmc, system_uuid)
    return _system_summary(system, lpars, vios_list)
