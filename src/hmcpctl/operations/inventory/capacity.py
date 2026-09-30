"""Presentation-neutral managed-system capacity operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from hmcpctl.client.core import HMCClient
from hmcpctl.xmlutil import leaf_text

_MEMORY = "AssociatedSystemMemoryConfiguration"
_PROCESSORS = "AssociatedSystemProcessorConfiguration"
_Number = TypeVar("_Number", int, float)


@dataclass(frozen=True)
class CapacitySummary:
    """Capacity totals and availability for one managed system.

    Figures are the system's own: total is its configurable memory (MiB) or
    processor units, free is what it currently reports available, and assigned
    is total minus free, so it includes hypervisor memory and every partition,
    VIOS included.
    """

    system_uuid: str | None
    system_name: str
    total_memory_mib: int
    assigned_memory_mib: int
    free_memory_mib: int
    total_proc_units: float
    assigned_proc_units: float
    free_proc_units: float
    total_lpars: int
    running_lpars: int


@dataclass(frozen=True)
class SystemCapacity:
    """Configurable and currently available capacity one system reports."""

    total_memory_mib: int
    free_memory_mib: int
    total_proc_units: float
    free_proc_units: float


def system_capacity(system: dict[str, Any]) -> SystemCapacity:
    """Read a ManagedSystem entry's configurable and available capacity.

    V10R3 nests these figures in its memory and processor configuration
    containers. A missing container or figure raises ``ValueError`` rather than
    reading as zero.
    """
    resource = system.get("Resource") or {}
    identity = system.get("UUID") or resource.get("SystemName") or "unknown system"
    return SystemCapacity(
        total_memory_mib=_figure(
            resource, _MEMORY, "ConfigurableSystemMemory", identity, int
        ),
        free_memory_mib=_figure(
            resource, _MEMORY, "CurrentAvailableSystemMemory", identity, int
        ),
        total_proc_units=_figure(
            resource, _PROCESSORS, "ConfigurableSystemProcessorUnits", identity, float
        ),
        free_proc_units=_figure(
            resource,
            _PROCESSORS,
            "CurrentAvailableSystemProcessorUnits",
            identity,
            float,
        ),
    )


def _figure(
    resource: dict[str, Any],
    container: str,
    field: str,
    identity: object,
    convert: Callable[[str], _Number],
) -> _Number:
    parent = resource.get(container)
    text = leaf_text(parent.get(field)) if isinstance(parent, dict) else None
    raw_value = text if isinstance(text, str) else None
    if raw_value is None:
        raise ValueError(
            f"Managed system {identity!r} reports no {container}/{field}, so its "
            "capacity is unknown. Inspect the system with hmc_list_systems before "
            "retrying."
        )
    try:
        return convert(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"Managed system {identity!r} has invalid {container}/{field} {raw_value!r}"
        ) from exc


def calculate_system_capacity(
    system: dict[str, Any], lpars: list[dict[str, Any]]
) -> CapacitySummary:
    """Compute capacity statistics for one managed system."""
    resource = system.get("Resource") or {}
    capacity = system_capacity(system)
    running = sum(
        (lpar.get("Resource") or {}).get("PartitionState") == "running"
        for lpar in lpars
    )
    return CapacitySummary(
        system_uuid=system.get("UUID"),
        system_name=resource.get("SystemName", ""),
        total_memory_mib=capacity.total_memory_mib,
        assigned_memory_mib=capacity.total_memory_mib - capacity.free_memory_mib,
        free_memory_mib=capacity.free_memory_mib,
        total_proc_units=capacity.total_proc_units,
        assigned_proc_units=round(
            capacity.total_proc_units - capacity.free_proc_units, 4
        ),
        free_proc_units=capacity.free_proc_units,
        total_lpars=len(lpars),
        running_lpars=running,
    )


async def fetch_capacity_report(hmc: HMCClient) -> list[CapacitySummary]:
    """Return capacity statistics for every managed system."""
    systems = await hmc.list_managed_systems()
    result = []
    for system in systems:
        uuid = system.get("UUID")
        lpars = await hmc.list_logical_partitions(uuid) if uuid else []
        result.append(calculate_system_capacity(system, lpars))
    return result


async def find_placement(
    hmc: HMCClient,
    desired_memory_mib: int,
    desired_proc_units: float = 0.5,
) -> list[CapacitySummary]:
    """Return systems with sufficient free resources, best fit first."""
    report = await fetch_capacity_report(hmc)
    candidates = [
        capacity
        for capacity in report
        if capacity.free_memory_mib >= desired_memory_mib
        and capacity.free_proc_units >= desired_proc_units
    ]
    candidates.sort(
        key=lambda capacity: (
            capacity.free_memory_mib,
            capacity.free_proc_units,
            capacity.system_name,
            capacity.system_uuid or "",
        )
    )
    return candidates
