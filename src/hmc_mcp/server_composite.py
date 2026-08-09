"""Composite MCP tools that aggregate data from multiple HMC endpoints in one call."""

from __future__ import annotations

from typing import Any

from ._app import (
    _READ_ONLY,
    _resolve_lpar_uuid,
    _run,
    mcp,
)
from .common import client_from_env


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
        "current_memory_mb": res.get("CurrentMemory") or res.get("DesiredMemory"),
        "desired_memory_mb": res.get("DesiredMemory"),
        # Current CPU: shared-processor units or dedicated CPUs
        "current_proc_units": res.get("CurrentProcessingUnits") or res.get("DesiredProcessingUnits"),
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
        #  filtered by LPAR link) and is not included here. Use hmc_vios() to
        #  retrieve per-VIOS storage mappings, then filter by the LPAR's
        #  partition ID.
        "mapped_storage": None,
    }


@mcp.tool(annotations=_READ_ONLY)
def hmc_lpar_summary(lpar_name_or_uuid: str) -> dict[str, Any]:
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
      out of scope for this best-effort summary. Use ``hmc_vios`` for per-VIOS
      storage mappings.

    Raises ``ValueError`` when the partition cannot be found.
    """
    async def _go() -> dict[str, Any]:
        async with client_from_env() as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            lpar, adapters = await _fetch_lpar_data(hmc, lpar_uuid)
            return _lpar_summary(lpar, adapters)

    return _run(_go)


async def _fetch_lpar_data(
    hmc, lpar_uuid: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch LPAR entry and its client network adapters concurrently."""
    import asyncio
    lpar_task = asyncio.create_task(hmc.get_logical_partition(lpar_uuid))
    adapters_task = asyncio.create_task(
        hmc.list_child("LogicalPartition", lpar_uuid, "ClientNetworkAdapter")
    )
    lpar = await lpar_task
    adapters = await adapters_task
    if lpar is None:
        raise ValueError(
            f"LPAR {lpar_uuid!r} not found after resolution. "
            "Use hmc_lpars to list available partitions."
        )
    return lpar, adapters
