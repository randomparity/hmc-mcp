"""Presentation-neutral normalized PCIe inventory contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, Literal, TypeVar

from hmc_mcp.config import HMCConfig
from hmc_mcp.client import HMCClient
from hmc_mcp.operations.ownership import resolve_and_authorize_lpar_names
from hmc_mcp.ssh.network import (
    SriovMode,
    assign_sriov_logical_port_dynamic,
    list_dedicated_pcie_slot_rows,
    list_sriov_adapter_rows,
    list_sriov_configured_logical_port_rows,
    list_sriov_physical_port_rows,
    list_sriov_unconfigured_logical_port_rows,
    read_sriov_lpar_state,
    read_sriov_environment,
    read_sriov_profile_ports,
    unassign_sriov_logical_port_profile,
    validate_sriov_mode,
)
from hmc_mcp.operations.pcie_validation import (
    require_command_safe_text,
    validate_capacity_percent,
)
from hmc_mcp.ssh.selectors import resolve_ssh_names


CapabilityState = Literal["available", "capability-unavailable"]
ResourceKind = Literal[
    "dedicated_slot",
    "sriov_adapter",
    "sriov_physical_port",
    "sriov_logical_port",
]
SRIOV_UNAVAILABLE_REASON = "ADR 0053 admits selectors but no SR-IOV read projection"
_ADMITTED_HMC_RELEASE = "V10R3 M1060"
_ADMITTED_SYSTEM_MODEL = "8375-42A"
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


@dataclass(frozen=True)
class _SriovAssignmentReadback:
    effective: SriovLogicalPortSnapshot | None
    profile: str | None
    error: Exception | None


@dataclass(frozen=True)
class _SriovAssignmentPreflight:
    config: HMCConfig
    system_name: str
    lpar_name: str
    selector: InventorySelector
    capacity: Decimal
    effective_before: SriovLogicalPortSnapshot | None
    profile_before: str
    idempotent_result: SriovLogicalPortChangeResult | None = None


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
    system_name, lpar_name = await resolve_and_authorize_lpar_names(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    raise PcieAssignmentUnavailableError(PCIE_ASSIGNMENT_UNAVAILABLE_REASON)


async def _system_name(config: HMCConfig, system: str) -> str:
    system_name, _ = await resolve_ssh_names(config, system, None)
    return system_name


async def list_dedicated_slots(
    hmc: HMCClient,
    system_name_or_uuid: str,
) -> InventoryResult[DedicatedSlot]:
    """List dedicated PCIe slots with stable identity and explicit unknowns."""
    config = hmc.config
    system_name = await _system_name(config, system_name_or_uuid)
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


async def require_admitted_environment(config: HMCConfig, system_name: str) -> None:
    version, model = await read_sriov_environment(config, system_name)
    normalized = " ".join(version.split()).lower()
    admitted = _ADMITTED_HMC_RELEASE.lower() in normalized or all(
        marker in normalized
        for marker in ("version: 10", "release: 3", "service pack: 1060")
    )
    if not admitted or model != _ADMITTED_SYSTEM_MODEL:
        raise SriovLogicalPortCapabilityError(
            "SR-IOV operations are admitted only for HMC V10R3 M1060 "
            "with managed-system model 8375-42A"
        )


async def _read_assignment_state(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    profile_name: str,
    adapter_id: str,
    logical_port_id: str,
) -> _SriovAssignmentReadback:
    effective = None
    profile = None
    error: Exception | None = None
    try:
        rows = await list_sriov_configured_logical_port_rows(
            config, system_name, adapter_id
        )
        matching = [row for row in rows if row["logical_port_id"] == logical_port_id]
        if len(matching) > 1:
            raise ValueError("duplicate logical-port inventory rows")
        effective = _snapshot(matching[0]) if matching else None
    except Exception as caught:
        error = caught
    try:
        profile = (
            await read_sriov_profile_ports(config, system_name, lpar_name, profile_name)
        )["sriov_eth_logical_ports"]
    except Exception as caught:
        error = error or caught
    return _SriovAssignmentReadback(effective, profile, error)


async def _read_sriov_assignment_inventory(
    config: HMCConfig,
    system_name: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
) -> tuple[dict[str, str], list[dict[str, str]], SriovLogicalPortSnapshot | None]:
    """Validate the selected adapter and physical port, then read assignment state."""
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
    if len(matching) > 1:
        raise ValueError("duplicate logical-port inventory rows")
    before = _snapshot(matching[0]) if matching else None
    return physical[0], rows, before


async def _require_sriov_assignment_capacity_and_state(
    config: HMCConfig,
    system_name: str,
    lpar_name: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    physical_port: dict[str, str],
    configured_rows: list[dict[str, str]],
    capacity: Decimal,
) -> None:
    """Require an available logical port, sufficient capacity, and mutable LPAR state."""
    candidates = await list_sriov_unconfigured_logical_port_rows(config, system_name)
    port_location = physical_port["phys_port_loc"] + "-S"
    if not any(
        row.get("adapter_id") == adapter_id
        and row.get("logical_port_id") == logical_port_id
        and row.get("location_code", "").startswith(port_location)
        for row in candidates
    ):
        raise ValueError(
            "logical port is not an unconfigured member of the selected physical port"
        )
    total = Decimal()
    seen: set[str] = set()
    for row in configured_rows:
        if row["phys_port_id"] != physical_port_id:
            continue
        if row["logical_port_id"] in seen:
            raise ValueError("duplicate logical-port inventory rows")
        seen.add(row["logical_port_id"])
        total += validate_capacity_percent(Decimal(row["capacity"]))
    if total + capacity > 100:
        raise ValueError(f"capacity exhausted: {total}% used of 100%")
    state = await read_sriov_lpar_state(config, system_name, lpar_name)
    if state["state"] == "Running" and state["rmc_state"] != "active":
        raise SriovLogicalPortCapabilityError("Running assignment requires active RMC")
    if state["state"] not in {"Running", "Not Activated"}:
        raise SriovLogicalPortCapabilityError(
            f"unsupported LPAR state: {state['state']}"
        )


async def _preflight_sriov_assignment(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    capacity_percent: Decimal,
    profile_name: str,
    ownership_override: bool,
) -> _SriovAssignmentPreflight:
    selector = InventorySelector(
        require_command_safe_text(adapter_id, "adapter_id"),
        require_command_safe_text(physical_port_id, "physical_port_id"),
        require_command_safe_text(logical_port_id, "logical_port_id"),
    )
    capacity = validate_capacity_percent(capacity_percent)
    system_name, lpar_name = await resolve_and_authorize_lpar_names(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    config = hmc.config
    require_command_safe_text(profile_name, "profile_name")
    await require_admitted_environment(config, system_name)
    physical, rows, before = await _read_sriov_assignment_inventory(
        config,
        system_name,
        adapter_id,
        physical_port_id,
        logical_port_id,
    )
    profile_before = (
        await read_sriov_profile_ports(config, system_name, lpar_name, profile_name)
    )["sriov_eth_logical_ports"]
    if before:
        if before.physical_port_id != physical_port_id:
            raise ValueError("logical port is assigned on a different physical port")
        if before.owner_lpar != lpar_name:
            raise PermissionError(
                f"logical port {logical_port_id} is already assigned to {before.owner_lpar}"
            )
        if before.capacity_percent != capacity:
            raise ValueError(
                "logical port is already assigned with a different capacity"
            )
        return _SriovAssignmentPreflight(
            config,
            system_name,
            lpar_name,
            selector,
            capacity,
            before,
            profile_before,
            SriovLogicalPortChangeResult(
                operation="assign",
                path="dynamic",
                changed=False,
                selector=selector,
                effective_before=before,
                effective_after=before,
                profile_before=profile_before,
                profile_after=profile_before,
                output="",
            ),
        )
    await _require_sriov_assignment_capacity_and_state(
        config,
        system_name,
        lpar_name,
        adapter_id,
        physical_port_id,
        logical_port_id,
        physical,
        rows,
        capacity,
    )
    return _SriovAssignmentPreflight(
        config, system_name, lpar_name, selector, capacity, before, profile_before
    )


async def assign_sriov_logical_port(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    capacity_percent: Decimal,
    *,
    profile_name: str,
    ownership_override: bool = False,
) -> SriovLogicalPortChangeResult:
    """Assign an SR-IOV logical port and reconcile the resulting state.

    Raises:
        ValueError: If a selector or requested capacity is invalid.
        SriovLogicalPortCapabilityError: If current inventory forbids assignment.
        SriovLogicalPortPartialError: If a dispatched mutation cannot be reconciled.
    """
    preflight = await _preflight_sriov_assignment(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        adapter_id,
        physical_port_id,
        logical_port_id,
        capacity_percent,
        profile_name,
        ownership_override,
    )
    if preflight.idempotent_result is not None:
        return preflight.idempotent_result
    config = preflight.config
    system_name = preflight.system_name
    lpar_name = preflight.lpar_name
    selector = preflight.selector
    capacity = preflight.capacity
    before = preflight.effective_before
    profile_before = preflight.profile_before
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
    readback = await _read_assignment_state(
        config,
        system_name,
        lpar_name,
        profile_name,
        adapter_id,
        logical_port_id,
    )
    after = readback.effective
    profile_after = readback.profile
    result = SriovLogicalPortChangeResult(
        operation="assign",
        path="dynamic",
        changed=True,
        selector=selector,
        effective_before=before,
        effective_after=after,
        profile_before=profile_before,
        profile_after=profile_after,
        output=output,
    )
    if (
        error
        or readback.error
        or not after
        or after.physical_port_id != physical_port_id
        or after.functional_state != "1"
        or after.owner_lpar != lpar_name
        or after.capacity_percent != capacity
        or profile_after != profile_before
    ):
        partial = SriovLogicalPortPartialError(
            f"assignment could not be verified: {error or readback.error or 'readback mismatch'}",
            result,
        )
        cause = error or readback.error
        if cause is not None:
            raise partial from cause
        raise partial
    return result


async def unassign_sriov_logical_port(
    hmc: HMCClient,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    *,
    profile_name: str,
    ownership_override: bool = False,
) -> SriovLogicalPortChangeResult:
    """Unassign an SR-IOV logical port from an inactive profile.

    Raises:
        ValueError: If selectors or the profile record are invalid.
        SriovLogicalPortCapabilityError: If current state forbids unassignment.
        SriovLogicalPortPartialError: If a dispatched mutation cannot be reconciled.
    """
    selector = InventorySelector(
        require_command_safe_text(adapter_id, "adapter_id"),
        require_command_safe_text(physical_port_id, "physical_port_id"),
        require_command_safe_text(logical_port_id, "logical_port_id"),
    )
    require_command_safe_text(profile_name, "profile_name")
    system_name, lpar_name = await resolve_and_authorize_lpar_names(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    config = hmc.config
    await require_admitted_environment(config, system_name)
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
            operation="unassign",
            path="profile",
            changed=False,
            selector=selector,
            effective_before=None,
            effective_after=None,
            profile_before=before,
            profile_after=before,
            output="",
        )
    parts = before.split(":")
    if (
        "," in before
        or len(parts) < 14
        or (parts[1], parts[2], parts[3])
        != (
            adapter_id,
            physical_port_id,
            logical_port_id,
        )
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
    after = None
    read_error: Exception | None = None
    try:
        after = (
            await read_sriov_profile_ports(config, system_name, lpar_name, profile_name)
        )["sriov_eth_logical_ports"]
    except Exception as caught:
        read_error = caught
    result = SriovLogicalPortChangeResult(
        operation="unassign",
        path="profile",
        changed=True,
        selector=selector,
        effective_before=None,
        effective_after=None,
        profile_before=before,
        profile_after=after,
        output=output,
    )
    if error or read_error or after != "none":
        partial = SriovLogicalPortPartialError(
            f"unassignment could not be verified: {error or read_error or 'readback mismatch'}",
            result,
        )
        cause = error or read_error
        if cause is not None:
            raise partial from cause
        raise partial
    return result


async def set_sriov_adapter_mode(
    hmc: HMCClient, system_name_or_uuid: str, adapter_id: str, mode: SriovMode
) -> str:
    """Confirm an adapter already has the requested admitted mode.

    Raises:
        ValueError: If the mode or adapter selector is invalid.
        SriovLogicalPortCapabilityError: If a mode transition would be required.
    """
    config = hmc.config
    validate_sriov_mode(mode)
    system_name = await _system_name(config, system_name_or_uuid)
    await require_admitted_environment(config, system_name)
    rows = [
        row
        for row in await list_sriov_adapter_rows(config, system_name)
        if row["adapter_id"] == require_command_safe_text(adapter_id, "adapter_id")
    ]
    if len(rows) == 1 and rows[0]["config_state"] == mode:
        return f"Adapter {adapter_id} already in {mode} mode"
    raise SriovLogicalPortCapabilityError(
        "adapter mode transition is not admitted by ADR 0056"
    )


async def list_sriov_adapters(
    hmc: HMCClient,
    system_name_or_uuid: str,
    adapter_id: str | None = None,
) -> InventoryResult[SriovAdapter]:
    """Return the evidence-bounded SR-IOV adapter capability state."""
    config = hmc.config
    system_name = await _system_name(config, system_name_or_uuid)
    try:
        await require_admitted_environment(config, system_name)
    except SriovLogicalPortCapabilityError as caught:
        return _unavailable(
            "sriov_adapter", system_name, InventorySelector(adapter_id), str(caught)
        )
    rows = await list_sriov_adapter_rows(config, system_name)
    items = [
        SriovAdapter(
            system_name,
            row["adapter_id"],
            row["config_state"],
            row["functional_state"],
            row["phys_loc"],
            None,
            None,
            None,
        )
        for row in rows
        if adapter_id is None or row["adapter_id"] == adapter_id
    ]
    return InventoryResult(
        "sriov_adapter",
        "available",
        system_name,
        InventorySelector(adapter_id),
        items,
        None,
    )


async def list_sriov_physical_ports(
    hmc: HMCClient,
    system_name_or_uuid: str,
    adapter_id: str | None = None,
    physical_port_id: str | None = None,
) -> InventoryResult[SriovPhysicalPort]:
    """Return the evidence-bounded SR-IOV physical-port capability state."""
    config = hmc.config
    system_name = await _system_name(config, system_name_or_uuid)
    selector = InventorySelector(adapter_id, physical_port_id)
    try:
        await require_admitted_environment(config, system_name)
    except SriovLogicalPortCapabilityError as caught:
        return _unavailable("sriov_physical_port", system_name, selector, str(caught))
    if adapter_id is None:
        raise ValueError("adapter_id is required for SR-IOV physical-port inventory")
    rows = await list_sriov_physical_port_rows(config, system_name, adapter_id)
    items = [
        SriovPhysicalPort(
            system_name,
            row["adapter_id"],
            row["phys_port_id"],
            row["state"],
            row["phys_port_loc"],
            None,
            None,
            None,
            None,
        )
        for row in rows
        if physical_port_id is None or row["phys_port_id"] == physical_port_id
    ]
    return InventoryResult(
        "sriov_physical_port", "available", system_name, selector, items, None
    )


async def list_sriov_logical_ports(
    hmc: HMCClient,
    system_name_or_uuid: str,
    adapter_id: str | None = None,
    physical_port_id: str | None = None,
    logical_port_id: str | None = None,
) -> InventoryResult[SriovLogicalPort]:
    """Return the evidence-bounded SR-IOV logical-port capability state."""
    config = hmc.config
    system_name = await _system_name(config, system_name_or_uuid)
    selector = InventorySelector(adapter_id, physical_port_id, logical_port_id)
    try:
        await require_admitted_environment(config, system_name)
    except SriovLogicalPortCapabilityError as caught:
        return _unavailable("sriov_logical_port", system_name, selector, str(caught))
    if adapter_id is None:
        raise ValueError("adapter_id is required for SR-IOV logical-port inventory")
    configured = await list_sriov_configured_logical_port_rows(
        config, system_name, adapter_id
    )
    items = [
        SriovLogicalPort(
            system_name,
            row["adapter_id"],
            row["phys_port_id"],
            row["logical_port_id"],
            row["functional_state"],
            _optional_text(row["lpar_name"]),
            _optional_text(row["lpar_id"]),
            Decimal(row["capacity"]),
            Decimal(row["max_capacity"]),
            None,
        )
        for row in configured
        if (physical_port_id is None or row["phys_port_id"] == physical_port_id)
        and (logical_port_id is None or row["logical_port_id"] == logical_port_id)
    ]
    unconfigured = await list_sriov_unconfigured_logical_port_rows(config, system_name)
    physical_rows = await list_sriov_physical_port_rows(config, system_name, adapter_id)

    def physical_id(row: dict[str, str]) -> str | None:
        location = row.get("location_code", "")
        matches = [
            port["phys_port_id"]
            for port in physical_rows
            if location.startswith(port["phys_port_loc"] + "-S")
        ]
        return matches[0] if len(matches) == 1 else None

    selected_unconfigured = [
        row for row in unconfigured if row.get("adapter_id") == adapter_id
    ]
    if any(physical_id(row) is None for row in selected_unconfigured):
        raise SriovLogicalPortCapabilityError(
            "unconfigured logical-port inventory has an ambiguous physical-port parent"
        )
    items.extend(
        SriovLogicalPort(
            system_name,
            row["adapter_id"],
            physical_id(row),
            row["logical_port_id"],
            "unconfigured",
            None,
            None,
            None,
            None,
            None,
        )
        for row in selected_unconfigured
        if (physical_port_id is None or physical_id(row) == physical_port_id)
        and (logical_port_id is None or row.get("logical_port_id") == logical_port_id)
    )
    return InventoryResult(
        "sriov_logical_port", "available", system_name, selector, items, None
    )


def _unavailable(
    resource_kind: ResourceKind,
    system: str,
    selector: InventorySelector,
    reason: str = SRIOV_UNAVAILABLE_REASON,
) -> InventoryResult:
    return InventoryResult(
        resource_kind,
        "capability-unavailable",
        system,
        selector,
        [],
        reason,
    )
