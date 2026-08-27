"""Declarative PCIe assignment validation and orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from hmc_mcp.client import HMCClient
from hmc_mcp.operations_pcie import (
    PCIE_ASSIGNMENT_UNAVAILABLE_REASON,
    PcieAssignmentUnavailableError,
    assign_dedicated_pcie_slot,
    assign_sriov_logical_port,
    list_sriov_adapters,
    list_sriov_logical_ports,
    list_sriov_physical_ports,
)
from hmc_mcp.operations_ssh_network import VnicBackingSelector, add_vnic
from hmc_mcp.ssh_network import (
    list_sriov_configured_logical_port_rows,
    list_vnic_backing_rows,
    read_vios_identity,
)


@dataclass(frozen=True)
class DedicatedPcieAssignment:
    """Dedicated-slot profile assignment request."""

    profile_name: str = field(metadata={"description": "Target HMC profile name."})
    drc_index: str = field(metadata={"description": "Dedicated-slot DRC index."})


@dataclass(frozen=True)
class SriovLogicalPortAssignment:
    """Direct SR-IOV logical-port assignment request."""

    profile_name: str = field(metadata={"description": "Target HMC profile name."})
    adapter_id: str = field(metadata={"description": "SR-IOV adapter identifier."})
    physical_port_id: str = field(metadata={"description": "Physical-port identifier."})
    logical_port_id: str = field(metadata={"description": "Logical-port identifier."})
    capacity_percent: Decimal = field(
        metadata={"description": "Requested decimal capacity percentage."}
    )


@dataclass(frozen=True)
class VnicAssignment:
    """vNIC assignment request using the ADR 0057 backing selector."""

    backing: VnicBackingSelector = field(
        metadata={"description": "Evidence-bounded VIOS SR-IOV backing selector."}
    )
    port_vlan_id: int = field(metadata={"description": "Port VLAN ID from 0 to 4094."})


@dataclass(frozen=True)
class LparPcieAssignments:
    """Shared declarative assignment vocabulary for LPAR workflows."""

    dedicated: tuple[DedicatedPcieAssignment, ...] = field(
        default_factory=tuple,
        metadata={"description": "Ordered dedicated-slot assignments."},
    )
    sriov: tuple[SriovLogicalPortAssignment, ...] = field(
        default_factory=tuple,
        metadata={"description": "Ordered direct SR-IOV logical-port assignments."},
    )
    vnics: tuple[VnicAssignment, ...] = field(
        default_factory=tuple,
        metadata={"description": "Ordered vNIC assignments."},
    )


@dataclass(frozen=True)
class AssignmentStep:
    """Stable outcome for one ordered assignment request."""

    step: str
    status: Literal["ok", "error", "skipped", "dry_run"]
    result: Any = None


@dataclass(frozen=True)
class AssignmentResult:
    """Outcome of applying a declarative assignment collection."""

    workflow_completed: bool
    dry_run: bool
    steps: tuple[AssignmentStep, ...]


@dataclass(frozen=True)
class LparPcieWorkflowResult:
    """Create or modify outcome with recoverable ordered state."""

    resource_created: bool
    workflow_completed: bool
    lpar: dict[str, Any] | None
    ownership_stamped: bool | None
    steps: tuple[AssignmentStep, ...]
    warnings: tuple[str, ...]

    def __getitem__(self, key: str) -> Any:
        """Preserve dictionary-style access to the returned LPAR resource."""
        if self.lpar is None:
            raise KeyError(key)
        return self.lpar[key]


def _required(value: str, name: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    structural = {"/": "slash", ",": "comma", "=": "equals sign", '"': "double quote"}
    for character, label in structural.items():
        if character in value:
            raise ValueError(
                f"{name} contains {label}; it would alter HMC command structure"
            )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} contains a control character")
    return value


def _capacity(value: Decimal) -> Decimal:
    if not value.is_finite() or value < 1 or value > 100:
        raise ValueError("capacity_percent must be between 1 and 100")
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise ValueError("capacity_percent supports at most two decimal places")
    return value


def _request_names(assignments: LparPcieAssignments) -> list[str]:
    return [
        *(f"dedicated[{index}]" for index, _ in enumerate(assignments.dedicated)),
        *(f"sriov[{index}]" for index, _ in enumerate(assignments.sriov)),
        *(f"vnic[{index}]" for index, _ in enumerate(assignments.vnics)),
    ]


async def _existing_capacity(
    hmc: HMCClient, system_name: str, adapter: str, physical: str
) -> Decimal:
    """Reconcile direct and vNIC projections by complete logical-port identity."""
    observations: dict[tuple[str, str, str], tuple[str, Decimal]] = {}
    direct = await list_sriov_configured_logical_port_rows(
        hmc.config, system_name, adapter
    )
    backing = await list_vnic_backing_rows(hmc.config, system_name)
    for row, capacity_field, physical_field in (
        *((row, "capacity", "phys_port_id") for row in direct),
        *((row, "desired_capacity", "physical_port_id") for row in backing),
    ):
        row_adapter = row["adapter_id"]
        key = system_name, row_adapter, row["logical_port_id"]
        observation = row[physical_field], Decimal(row[capacity_field])
        if key in observations and observations[key] != observation:
            raise ValueError("conflicting cross-projection logical-port capacity")
        observations[key] = observation
    return sum(
        (
            capacity
            for (_, item_adapter, _), (item_physical, capacity) in observations.items()
            if item_adapter == adapter and item_physical == physical
        ),
        Decimal(),
    )


async def prevalidate_lpar_pcie_assignments(
    hmc: HMCClient,
    system: str,
    assignments: LparPcieAssignments,
) -> None:
    """Validate the complete collection without reserving or mutating resources."""
    if assignments.dedicated:
        for item in assignments.dedicated:
            _required(item.profile_name, "profile_name")
            _required(item.drc_index, "drc_index")
        raise PcieAssignmentUnavailableError(PCIE_ASSIGNMENT_UNAVAILABLE_REASON)

    identities: dict[tuple[str, str], tuple[str, Decimal]] = {}
    requested_capacity: dict[tuple[str, str], Decimal] = {}
    for item in assignments.sriov:
        _required(item.profile_name, "profile_name")
        adapter = _required(item.adapter_id, "adapter_id")
        physical = _required(item.physical_port_id, "physical_port_id")
        logical = _required(item.logical_port_id, "logical_port_id")
        capacity = _capacity(item.capacity_percent)
        key = adapter, logical
        observation = physical, capacity
        if key in identities:
            if identities[key] != observation:
                raise ValueError("conflicting duplicate logical-port assignment")
            raise ValueError("duplicate logical-port assignment")
        identities[key] = observation
        requested_capacity[adapter, physical] = (
            requested_capacity.get((adapter, physical), Decimal()) + capacity
        )

    vnic_identities: set[tuple[str, str, str, str, Decimal, int]] = set()
    for item in assignments.vnics:
        backing = item.backing
        adapter = _required(backing.adapter_id, "adapter_id")
        physical = _required(backing.physical_port_id, "physical_port_id")
        _required(backing.vios_name, "vios_name")
        _required(backing.vios_lpar_id, "vios_lpar_id")
        capacity = _capacity(backing.capacity_percent)
        if type(item.port_vlan_id) is not int or not 0 <= item.port_vlan_id <= 4094:
            raise ValueError("port_vlan_id must be an integer between 0 and 4094")
        identity = (
            backing.vios_name,
            backing.vios_lpar_id,
            adapter,
            physical,
            capacity,
            item.port_vlan_id,
        )
        if identity in vnic_identities:
            raise ValueError("duplicate vNIC assignment")
        vnic_identities.add(identity)
        requested_capacity[adapter, physical] = (
            requested_capacity.get((adapter, physical), Decimal()) + capacity
        )

    for adapter, physical in requested_capacity:
        adapters = await list_sriov_adapters(hmc.config, system, adapter)
        if adapters.capability != "available" or len(adapters.items) != 1:
            raise ValueError(f"SR-IOV adapter {adapter!r} is unavailable")
        if adapters.items[0].mode != "sriov" or adapters.items[0].availability != "1":
            raise ValueError(f"SR-IOV adapter {adapter!r} is not healthy")
        ports = await list_sriov_physical_ports(hmc.config, system, adapter, physical)
        if ports.capability != "available" or len(ports.items) != 1:
            raise ValueError(
                f"SR-IOV physical port {adapter}/{physical} is unavailable"
            )
        if ports.items[0].availability != "1":
            raise ValueError(
                f"SR-IOV physical port {adapter}/{physical} is not healthy"
            )
        logical = await list_sriov_logical_ports(hmc.config, system, adapter, physical)
        if logical.capability != "available":
            raise ValueError(
                logical.unavailable_reason or "logical-port inventory unavailable"
            )
        used = await _existing_capacity(hmc, adapters.system, adapter, physical)
        if used + requested_capacity[adapter, physical] > 100:
            raise ValueError(
                f"capacity exhausted on {adapter}/{physical}: {used}% used and "
                f"{requested_capacity[adapter, physical]}% requested"
            )
    checked_vios: set[tuple[str, str]] = set()
    for item in assignments.vnics:
        identity = item.backing.vios_name, item.backing.vios_lpar_id
        if identity in checked_vios:
            continue
        system_name = (await list_sriov_adapters(hmc.config, system)).system
        observed = await read_vios_identity(hmc.config, system_name, identity[0])
        if observed != {
            "name": identity[0],
            "lpar_id": identity[1],
            "lpar_env": "vioserver",
        }:
            raise ValueError(
                "selected VIOS name, ID, or partition type does not match inventory"
            )
        checked_vios.add(identity)


async def apply_lpar_pcie_assignments(
    hmc: HMCClient,
    system: str,
    lpar: str,
    assignments: LparPcieAssignments,
    *,
    dry_run: bool = False,
    ownership_override: bool = False,
) -> AssignmentResult:
    """Apply requests in stable order and expose partial state without rollback."""
    await prevalidate_lpar_pcie_assignments(hmc, system, assignments)
    return await _apply_validated_lpar_pcie_assignments(
        hmc,
        system,
        lpar,
        assignments,
        dry_run=dry_run,
        ownership_override=ownership_override,
    )


async def _apply_validated_lpar_pcie_assignments(
    hmc: HMCClient,
    system: str,
    lpar: str,
    assignments: LparPcieAssignments,
    *,
    dry_run: bool = False,
    ownership_override: bool = False,
) -> AssignmentResult:
    """Execute a collection validated by the enclosing atomic workflow."""
    names = _request_names(assignments)
    if dry_run:
        return AssignmentResult(
            False, True, tuple(AssignmentStep(n, "dry_run") for n in names)
        )

    operations: list[tuple[str, Any]] = []
    operations.extend(
        (
            f"dedicated[{index}]",
            lambda item=item: assign_dedicated_pcie_slot(
                hmc,
                system,
                lpar,
                item.profile_name,
                item.drc_index,
                ownership_override=ownership_override,
            ),
        )
        for index, item in enumerate(assignments.dedicated)
    )
    operations.extend(
        (
            f"sriov[{index}]",
            lambda item=item: assign_sriov_logical_port(
                hmc,
                system,
                lpar,
                item.adapter_id,
                item.physical_port_id,
                item.logical_port_id,
                item.capacity_percent,
                profile_name=item.profile_name,
                ownership_override=ownership_override,
            ),
        )
        for index, item in enumerate(assignments.sriov)
    )
    operations.extend(
        (
            f"vnic[{index}]",
            lambda item=item: add_vnic(
                hmc,
                system,
                lpar,
                item.backing,
                item.port_vlan_id,
                ownership_override=ownership_override,
            ),
        )
        for index, item in enumerate(assignments.vnics)
    )
    steps: list[AssignmentStep] = []
    for index, (name, operation) in enumerate(operations):
        try:
            steps.append(AssignmentStep(name, "ok", await operation()))
        except Exception as error:
            result = getattr(error, "result", str(error))
            steps.append(AssignmentStep(name, "error", result))
            steps.extend(
                AssignmentStep(rest, "skipped") for rest, _ in operations[index + 1 :]
            )
            return AssignmentResult(False, False, tuple(steps))
    return AssignmentResult(True, False, tuple(steps))
