"""Presentation-neutral normalized PCIe inventory contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, Literal, TypeVar, cast

from hmc_mcp.config import HMCConfig
from hmc_mcp.ssh_commands import list_dedicated_pcie_slot_rows
from hmc_mcp.ssh_selectors import resolve_ssh_names


CapabilityState = Literal["available", "capability-unavailable"]
ResourceKind = Literal[
    "dedicated_slot",
    "sriov_adapter",
    "sriov_physical_port",
    "sriov_logical_port",
]
SRIOV_UNAVAILABLE_REASON = "ADR 0053 admits selectors but no SR-IOV read projection"

_T = TypeVar("_T")


@dataclass(frozen=True)
class InventorySelector:
    """Optional selector scope copied from the caller without inference."""

    adapter_id: str | None = None
    physical_port_id: str | None = None
    logical_port_id: str | None = None


@dataclass(frozen=True)
class InventoryResult(Generic[_T]):
    """Stable collection envelope separating availability from capability."""

    resource_kind: ResourceKind
    capability: CapabilityState
    system: str
    selector: InventorySelector
    items: list[_T]
    unavailable_reason: str | None


@dataclass(frozen=True)
class DedicatedSlot:
    system: str
    drc_index: str
    description: str | None
    owner_lpar: str | None
    availability: str | None


@dataclass(frozen=True)
class SriovAdapter:
    system: str
    adapter_id: str
    mode: str | None
    availability: str | None
    location_code: str | None
    owner_lpar: str | None
    logical_ports_in_use: int | None
    logical_ports_available: int | None


@dataclass(frozen=True)
class SriovPhysicalPort:
    system: str
    adapter_id: str
    physical_port_id: str
    availability: str | None
    location_code: str | None
    owner_lpar: str | None
    minimum_capacity_granularity_percent: Decimal | None
    logical_ports_in_use: int | None
    logical_ports_available: int | None


@dataclass(frozen=True)
class SriovLogicalPort:
    system: str
    adapter_id: str
    physical_port_id: str | None
    logical_port_id: str
    availability: str | None
    owner_lpar: str | None
    owner_lpar_id: str | None
    capacity_percent: Decimal | None
    maximum_capacity_percent: Decimal | None
    compatibility: str | None


async def _system_name(config: HMCConfig, system: str) -> str:
    system_name, _ = await resolve_ssh_names(config, system, None)
    return cast(str, system_name)


async def list_dedicated_slots(
    config: HMCConfig,
    system: str,
) -> InventoryResult[DedicatedSlot]:
    """List dedicated PCIe slots with stable identity and explicit unknowns."""
    system_name = await _system_name(config, system)
    rows = await list_dedicated_pcie_slot_rows(config, system_name)
    items: list[DedicatedSlot] = []
    for row in rows:
        drc_index = row["drc_index"]
        if not drc_index.strip():
            raise ValueError("dedicated PCIe slot row has a blank drc_index")
        items.append(
            DedicatedSlot(
                system=system_name,
                drc_index=drc_index,
                description=_optional_text(row["description"]),
                owner_lpar=_optional_text(row["lpar_name"]),
                availability=None,
            )
        )
    return InventoryResult(
        "dedicated_slot", "available", system_name, InventorySelector(), items, None
    )


def _optional_text(value: str) -> str | None:
    return value if value.strip() else None


async def list_sriov_adapters(
    config: HMCConfig,
    system: str,
    adapter_id: str | None = None,
) -> InventoryResult[SriovAdapter]:
    """Return the evidence-bounded SR-IOV adapter capability state."""
    system_name = await _system_name(config, system)
    return _unavailable("sriov_adapter", system_name, InventorySelector(adapter_id))


async def list_sriov_physical_ports(
    config: HMCConfig,
    system: str,
    adapter_id: str | None = None,
    physical_port_id: str | None = None,
) -> InventoryResult[SriovPhysicalPort]:
    """Return the evidence-bounded SR-IOV physical-port capability state."""
    system_name = await _system_name(config, system)
    selector = InventorySelector(adapter_id, physical_port_id)
    return _unavailable("sriov_physical_port", system_name, selector)


async def list_sriov_logical_ports(
    config: HMCConfig,
    system: str,
    adapter_id: str | None = None,
    physical_port_id: str | None = None,
    logical_port_id: str | None = None,
) -> InventoryResult[SriovLogicalPort]:
    """Return the evidence-bounded SR-IOV logical-port capability state."""
    system_name = await _system_name(config, system)
    selector = InventorySelector(adapter_id, physical_port_id, logical_port_id)
    return _unavailable("sriov_logical_port", system_name, selector)


def _unavailable(
    resource_kind: ResourceKind,
    system: str,
    selector: InventorySelector,
) -> InventoryResult:
    return InventoryResult(
        resource_kind,
        "capability-unavailable",
        system,
        selector,
        [],
        SRIOV_UNAVAILABLE_REASON,
    )
