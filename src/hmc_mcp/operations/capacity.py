"""Presentation-neutral managed-system capacity operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..client import HMCClient


@dataclass(frozen=True)
class CapacitySummary:
    """Capacity totals and availability for one managed system."""

    system_uuid: str | None
    system_name: str
    total_memory_mb: int
    assigned_memory_mb: int
    free_memory_mb: int
    total_proc_units: float
    assigned_proc_units: float
    free_proc_units: float
    total_lpars: int
    running_lpars: int


def lpar_processing_units(lpar: dict[str, Any]) -> float:
    """Return desired processing units, rejecting malformed HMC inventory."""
    resource = lpar.get("Resource") or {}
    raw_value = resource.get("DesiredProcessingUnits")
    if raw_value in (None, ""):
        return 0.0
    try:
        return float(raw_value)
    except (TypeError, ValueError) as exc:
        identity = lpar.get("UUID") or resource.get("PartitionName") or "unknown LPAR"
        raise ValueError(
            f"LPAR {identity!r} has invalid DesiredProcessingUnits {raw_value!r}"
        ) from exc


def system_capacity(
    system: dict[str, Any], lpars: list[dict[str, Any]]
) -> CapacitySummary:
    """Compute capacity statistics for one managed system."""
    resource = system.get("Resource") or {}
    total_memory = int(resource.get("AssignableSystemMemory") or 0)
    total_processors = float(resource.get("ConfigurableSystemProcessorUnits") or 0.0)
    assigned_memory = 0
    assigned_processors = 0.0
    running = 0
    for lpar in lpars:
        lpar_resource = lpar.get("Resource") or {}
        assigned_memory += int(lpar_resource.get("DesiredMemory") or 0)
        assigned_processors += lpar_processing_units(lpar)
        if lpar_resource.get("PartitionState") == "running":
            running += 1
    return CapacitySummary(
        system_uuid=system.get("UUID"),
        system_name=resource.get("SystemName", ""),
        total_memory_mb=total_memory,
        assigned_memory_mb=assigned_memory,
        free_memory_mb=total_memory - assigned_memory,
        total_proc_units=total_processors,
        assigned_proc_units=round(assigned_processors, 4),
        free_proc_units=round(total_processors - assigned_processors, 4),
        total_lpars=len(lpars),
        running_lpars=running,
    )


async def capacity_report(hmc: HMCClient) -> list[CapacitySummary]:
    """Return capacity statistics for every managed system."""
    systems = await hmc.list_managed_systems()
    result = []
    for system in systems:
        uuid = system.get("UUID")
        lpars = await hmc.list_logical_partitions(uuid) if uuid else []
        result.append(system_capacity(system, lpars))
    return result


async def find_placement(
    hmc: HMCClient,
    desired_memory_mb: int,
    desired_proc_units: float = 0.5,
) -> list[CapacitySummary]:
    """Return systems with sufficient free resources, best fit first."""
    report = await capacity_report(hmc)
    candidates = [
        capacity
        for capacity in report
        if capacity.free_memory_mb >= desired_memory_mb
        and capacity.free_proc_units >= desired_proc_units
    ]
    candidates.sort(
        key=lambda capacity: (
            capacity.free_memory_mb,
            capacity.free_proc_units,
            capacity.system_name,
            capacity.system_uuid or "",
        )
    )
    return candidates
