"""Presentation-neutral normalized PCIe inventory contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, Literal, TypeVar, cast

from hmc_mcp.config import HMCConfig
from hmc_mcp.client import HMCClient
from hmc_mcp.common import resolve_lpar_uuid, resolve_system_uuid
from hmc_mcp.operations_lpar import (
    authorize_lpar_mutation,
    resolve_lpar_ownership_names,
)
from hmc_mcp.ssh_commands import (
    SriovMode,
    assign_sriov_logical_port_dynamic,
    list_dedicated_pcie_slot_rows,
    list_sriov_adapter_rows,
    list_sriov_configured_logical_port_rows,
    list_sriov_physical_port_rows,
    list_sriov_unconfigured_logical_port_rows,
    read_sriov_lpar_state,
    read_sriov_profile_ports,
    unassign_sriov_logical_port_profile,
    validate_sriov_mode,
)
from hmc_mcp.ssh_selectors import resolve_ssh_names


CapabilityState = Literal["available", "capability-unavailable"]
ResourceKind = Literal[
    "dedicated_slot",
    "sriov_adapter",
    "sriov_physical_port",
    "sriov_logical_port",
]
SRIOV_UNAVAILABLE_REASON = "ADR 0053 admits selectors but no SR-IOV read projection"
PCIE_ASSIGNMENT_UNAVAILABLE_REASON = (
    "ADR 0053 admits no exact dedicated PCIe profile readback; "
    "assignment cannot be safely verified"
)

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


@dataclass(frozen=True)
class SriovLogicalPortSnapshot:
    adapter_id: str
    physical_port_id: str
    logical_port_id: str
    owner_lpar: str
    owner_lpar_id: str
    capacity_percent: Decimal
    functional_state: str


@dataclass(frozen=True)
class SriovLogicalPortChangeResult:
    operation: Literal["assign", "unassign"]
    path: Literal["dynamic", "profile"]
    changed: bool
    selector: InventorySelector
    effective_before: SriovLogicalPortSnapshot | None
    effective_after: SriovLogicalPortSnapshot | None
    profile_before: str | None
    profile_after: str | None
    output: str


class SriovLogicalPortCapabilityError(RuntimeError):
    """Raised for an uncharacterized state/mutation matrix cell."""


class SriovLogicalPortPartialError(RuntimeError):
    """Raised when a dispatched mutation cannot be fully reconciled."""

    def __init__(self, message: str, result: SriovLogicalPortChangeResult):
        super().__init__(message)
        self.result = result


class PcieAssignmentUnavailableError(RuntimeError):
    """Raised when the evidence-backed capability matrix forbids mutation."""


async def assign_dedicated_pcie_slot(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    profile_name: str,
    drc_index: str,
    *,
    ownership_override: bool = False,
) -> None:
    """Authorize a dedicated-slot profile assignment and fail closed."""
    await _authorize_pcie_profile_request(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        profile_name,
        drc_index,
        ownership_override=ownership_override,
    )


async def unassign_dedicated_pcie_slot(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    profile_name: str,
    drc_index: str,
    *,
    ownership_override: bool = False,
) -> None:
    """Authorize a dedicated-slot profile unassignment and fail closed."""
    await _authorize_pcie_profile_request(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        profile_name,
        drc_index,
        ownership_override=ownership_override,
    )


async def _authorize_pcie_profile_request(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    profile_name: str,
    drc_index: str,
    *,
    ownership_override: bool,
) -> None:
    if not profile_name.strip():
        raise ValueError("profile_name must not be blank")
    if not drc_index.strip():
        raise ValueError("drc_index must not be blank")
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_uuid
    )
    system_name, lpar_name = await resolve_lpar_ownership_names(
        hmc, system_uuid, system_name_or_uuid, lpar_uuid
    )
    await authorize_lpar_mutation(
        hmc,
        system_name,
        lpar_name,
        ownership_override=ownership_override,
    )
    raise PcieAssignmentUnavailableError(PCIE_ASSIGNMENT_UNAVAILABLE_REASON)


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


def _required(value: str, name: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


def _capacity(value: Decimal) -> Decimal:
    if not value.is_finite() or value < 1 or value > 100:
        raise ValueError("capacity_percent must be between 1 and 100")
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise ValueError("capacity_percent supports at most two decimal places")
    return value


def _snapshot(row: dict[str, str]) -> SriovLogicalPortSnapshot:
    return SriovLogicalPortSnapshot(
        row["adapter_id"],
        row["phys_port_id"],
        row["logical_port_id"],
        row["lpar_name"],
        row["lpar_id"],
        Decimal(row["capacity"]),
        row["functional_state"],
    )


async def _resolve_lpar(
    hmc: HMCClient, system: str, lpar: str, override: bool
) -> tuple[str, str]:
    system_uuid = await resolve_system_uuid(hmc, system)
    lpar_uuid = await resolve_lpar_uuid(hmc, lpar, system_name_or_uuid=system_uuid)
    names = await resolve_lpar_ownership_names(hmc, system_uuid, system, lpar_uuid)
    await authorize_lpar_mutation(hmc, *names, ownership_override=override)
    return names


async def assign_sriov_logical_port(
    hmc: HMCClient,
    system: str,
    lpar: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    capacity_percent: Decimal,
    *,
    profile_name: str | None = None,
    ownership_override: bool = False,
) -> SriovLogicalPortChangeResult:
    selector = InventorySelector(
        _required(adapter_id, "adapter_id"),
        _required(physical_port_id, "physical_port_id"),
        _required(logical_port_id, "logical_port_id"),
    )
    capacity = _capacity(capacity_percent)
    system_name, lpar_name = await _resolve_lpar(hmc, system, lpar, ownership_override)
    config = hmc.config
    adapters = [
        row
        for row in await list_sriov_adapter_rows(config, system_name)
        if row["adapter_id"] == adapter_id
    ]
    if (
        len(adapters) != 1
        or adapters[0]["config_state"] != "sriov"
        or adapters[0]["functional_state"] != "1"
    ):
        raise SriovLogicalPortCapabilityError("adapter is not in healthy SR-IOV mode")
    physical = [
        row
        for row in await list_sriov_physical_port_rows(config, system_name, adapter_id)
        if row["phys_port_id"] == physical_port_id
    ]
    if len(physical) != 1 or physical[0]["state"] != "1":
        raise SriovLogicalPortCapabilityError("physical port is unavailable")
    rows = await list_sriov_configured_logical_port_rows(
        config, system_name, adapter_id
    )
    matching = [row for row in rows if row["logical_port_id"] == logical_port_id]
    before = _snapshot(matching[0]) if len(matching) == 1 else None
    if len(matching) > 1:
        raise ValueError("duplicate logical-port inventory rows")
    if before:
        if before.owner_lpar != lpar_name:
            raise PermissionError(
                f"logical port {logical_port_id} is already assigned to {before.owner_lpar}"
            )
        if before.capacity_percent != capacity:
            raise ValueError(
                "logical port is already assigned with a different capacity"
            )
        return SriovLogicalPortChangeResult(
            "assign", "dynamic", False, selector, before, before, None, None, ""
        )
    candidates = await list_sriov_unconfigured_logical_port_rows(config, system_name)
    port_location = physical[0]["phys_port_loc"] + "-S"
    if not any(
        row.get("adapter_id") == adapter_id
        and row.get("logical_port_id") == logical_port_id
        and row.get("location_code", "").startswith(port_location)
        for row in candidates
    ):
        raise ValueError(
            "logical port is not an unconfigured member of the selected physical port"
        )
    total = Decimal("0")
    seen: set[str] = set()
    for row in rows:
        if row["phys_port_id"] != physical_port_id:
            continue
        if row["logical_port_id"] in seen:
            raise ValueError("duplicate logical-port inventory rows")
        seen.add(row["logical_port_id"])
        total += _capacity(Decimal(row["capacity"]))
    if total + capacity > 100:
        raise ValueError(f"capacity exhausted: {total}% used of 100%")
    state = await read_sriov_lpar_state(config, system_name, lpar_name)
    if state["state"] == "Running" and state["rmc_state"] != "active":
        raise SriovLogicalPortCapabilityError("Running assignment requires active RMC")
    if state["state"] not in {"Running", "Not Activated"}:
        raise SriovLogicalPortCapabilityError(
            f"unsupported LPAR state: {state['state']}"
        )
    profile_before = None
    if profile_name:
        profile_before = (
            await read_sriov_profile_ports(config, system_name, lpar_name, profile_name)
        )["sriov_eth_logical_ports"]
    output = ""
    error: Exception | None = None
    try:
        output = await assign_sriov_logical_port_dynamic(
            config,
            system_name,
            lpar_name,
            adapter_id,
            physical_port_id,
            logical_port_id,
            str(capacity),
        )
    except Exception as caught:
        error = caught
    after_rows = await list_sriov_configured_logical_port_rows(
        config, system_name, adapter_id
    )
    after_match = [
        row for row in after_rows if row["logical_port_id"] == logical_port_id
    ]
    after = _snapshot(after_match[0]) if len(after_match) == 1 else None
    profile_after = None
    if profile_name:
        profile_after = (
            await read_sriov_profile_ports(config, system_name, lpar_name, profile_name)
        )["sriov_eth_logical_ports"]
    result = SriovLogicalPortChangeResult(
        "assign",
        "dynamic",
        True,
        selector,
        before,
        after,
        profile_before,
        profile_after,
        output,
    )
    if (
        error
        or not after
        or after.owner_lpar != lpar_name
        or after.capacity_percent != capacity
        or profile_after != profile_before
    ):
        raise SriovLogicalPortPartialError(
            f"assignment could not be verified: {error or 'readback mismatch'}", result
        )
    return result


async def unassign_sriov_logical_port(
    hmc: HMCClient,
    system: str,
    lpar: str,
    profile_name: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    *,
    ownership_override: bool = False,
) -> SriovLogicalPortChangeResult:
    selector = InventorySelector(
        _required(adapter_id, "adapter_id"),
        _required(physical_port_id, "physical_port_id"),
        _required(logical_port_id, "logical_port_id"),
    )
    _required(profile_name, "profile_name")
    system_name, lpar_name = await _resolve_lpar(hmc, system, lpar, ownership_override)
    config = hmc.config
    state = await read_sriov_lpar_state(config, system_name, lpar_name)
    if state["state"] != "Not Activated":
        raise SriovLogicalPortCapabilityError(
            "only Not Activated profile unassign is supported"
        )
    before = (
        await read_sriov_profile_ports(config, system_name, lpar_name, profile_name)
    )["sriov_eth_logical_ports"]
    if before == "none":
        return SriovLogicalPortChangeResult(
            "unassign", "profile", False, selector, None, None, before, before, ""
        )
    parts = before.split(":")
    if len(parts) < 14 or (parts[1], parts[2], parts[3]) != (
        adapter_id,
        physical_port_id,
        logical_port_id,
    ):
        raise ValueError("profile does not contain exactly the selected logical port")
    output = ""
    error: Exception | None = None
    try:
        output = await unassign_sriov_logical_port_profile(
            config, system_name, lpar_name, profile_name
        )
    except Exception as caught:
        error = caught
    after = (
        await read_sriov_profile_ports(config, system_name, lpar_name, profile_name)
    )["sriov_eth_logical_ports"]
    result = SriovLogicalPortChangeResult(
        "unassign", "profile", True, selector, None, None, before, after, output
    )
    if error or after != "none":
        raise SriovLogicalPortPartialError(
            f"unassignment could not be verified: {error or 'readback mismatch'}",
            result,
        )
    return result


async def set_sriov_adapter_mode(
    config: HMCConfig, system: str, adapter_id: str, mode: SriovMode
) -> str:
    validate_sriov_mode(mode)
    system_name = await _system_name(config, system)
    rows = [
        row
        for row in await list_sriov_adapter_rows(config, system_name)
        if row["adapter_id"] == _required(adapter_id, "adapter_id")
    ]
    if len(rows) == 1 and rows[0]["config_state"] == mode:
        return f"Adapter {adapter_id} already in {mode} mode"
    raise SriovLogicalPortCapabilityError(
        "adapter mode transition is not admitted by ADR 0056"
    )


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
