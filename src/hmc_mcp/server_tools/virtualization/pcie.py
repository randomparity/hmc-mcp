"""MCP tools for PCIe and SR-IOV configuration."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..._app import serialize_tool_result, ssh_with_client, with_client
from ...operations.virtualization.pcie import (
    InventorySelector,
    SriovLogicalPortChangeResult,
    SriovMode,
    assign_sriov_logical_port,
    list_dedicated_slots,
    list_sriov_adapters,
    list_sriov_logical_ports,
    list_sriov_physical_ports,
    set_sriov_adapter_mode,
    unassign_sriov_logical_port,
)
from ...ssh.io_inventory import PciClass, list_io_slots
from ...tool_registry import tool_module

tool, register_tools, tool_security = tool_module()


@tool(
    effect="read",
    operation="pcie.list_dedicated_slots",
    target_kind="managed_system",
)
def hmc_list_dedicated_pcie_slots(
    system_name_or_uuid: str,
    profile: str | None = None,
) -> dict[str, Any]:
    """List normalized dedicated PCIe slots with stable DRC identities."""

    async def slots(hmc: Any) -> Any:
        return serialize_tool_result(
            await list_dedicated_slots(hmc, system_name_or_uuid)
        )

    return with_client(slots, profile=profile)


@tool(effect="read", operation="pcie.list_sriov_adapters", target_kind="managed_system")
def hmc_list_sriov_adapters(
    system_name_or_uuid: str,
    adapter_id: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """List normalized SR-IOV adapters, or report capability unavailable."""

    async def adapters(hmc: Any) -> Any:
        return serialize_tool_result(
            await list_sriov_adapters(hmc, system_name_or_uuid, adapter_id)
        )

    return with_client(adapters, profile=profile)


@tool(
    effect="read",
    operation="pcie.list_sriov_physical_ports",
    target_kind="managed_system",
)
def hmc_list_sriov_physical_ports(
    system_name_or_uuid: str,
    adapter_id: str | None = None,
    physical_port_id: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """List normalized SR-IOV physical ports, or report capability unavailable."""

    async def ports(hmc: Any) -> Any:
        return serialize_tool_result(
            await list_sriov_physical_ports(
                hmc, system_name_or_uuid, adapter_id, physical_port_id
            )
        )

    return with_client(ports, profile=profile)


@tool(
    effect="read",
    operation="pcie.list_sriov_logical_ports",
    target_kind="managed_system",
)
def hmc_list_sriov_logical_ports(
    system_name_or_uuid: str,
    adapter_id: str | None = None,
    physical_port_id: str | None = None,
    logical_port_id: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """List normalized SR-IOV logical ports, or report capability unavailable."""

    async def ports(hmc: Any) -> Any:
        return serialize_tool_result(
            await list_sriov_logical_ports(
                hmc,
                system_name_or_uuid,
                adapter_id,
                physical_port_id,
                logical_port_id,
            )
        )

    return with_client(ports, profile=profile)


@tool(effect="read", operation="io_slot.list", target_kind="managed_system")
def hmc_list_io_slots(
    system_name_or_uuid: str,
    pci_class: PciClass = "all",
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List physical I/O slots, optionally filtered by PCI class."""

    return ssh_with_client(
        lambda config, system_name, _: list_io_slots(config, system_name, pci_class),
        system_name_or_uuid=system_name_or_uuid,
        profile=profile,
    )


@tool(effect="mutate", operation="sriov.set_mode", target_kind="managed_system")
def hmc_set_sriov_adapter_mode(
    system_name_or_uuid: str,
    adapter_id: str,
    mode: SriovMode,
    profile: str | None = None,
) -> str:
    """Verify that a physical adapter is already in the requested mode.

    The system may be given by CLI name or by UUID; a UUID is resolved to
    its CLI name via REST (falling back to an lssyscfg lookup over SSH when
    the REST API is unreachable) before the command runs.

    ``adapter_id`` is the physical adapter identifier as reported by
    ``hmc_list_io_slots``.

    ``mode`` must be one of:
      - ``"sriov"``      — enable SR-IOV mode (shared virtual functions)
      - ``"dedicated"``  — disable SR-IOV, use as a dedicated (passthrough) adapter

    Mode transitions are not admitted by the available same-family evidence and
    fail closed without mutation.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        adapter_id: Physical adapter ID returned by ``hmc_list_io_slots``.
        mode: ``sriov`` for shared virtual functions or ``dedicated`` for
            passthrough use.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    return with_client(
        lambda hmc: set_sriov_adapter_mode(hmc, system_name_or_uuid, adapter_id, mode),
        profile=profile,
    )


@tool(effect="mutate", operation="sriov.assign_logical_port", target_kind="lpar")
def hmc_assign_sriov_logical_port(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    capacity_percent: float,
    profile_name: str,
    ownership_override: bool = False,
    profile: str | None = None,
) -> SriovLogicalPortChangeResult:
    """Assign an evidence-backed Ethernet SR-IOV logical port.

    Args:
        system_name_or_uuid: Managed system name or UUID.
        lpar_name_or_uuid: Target partition name or UUID.
        adapter_id: Normalized SR-IOV adapter ID.
        physical_port_id: Normalized parent physical-port ID.
        logical_port_id: Normalized unconfigured logical-port ID.
        capacity_percent: Requested percentage capacity from 1 through 100.
        profile_name: Exact profile whose unchanged state is verified.
        ownership_override: Permit a separately approved ADR 0011 ownership override.
        profile: TOML connection profile name.
    """

    async def _go(hmc):
        return await assign_sriov_logical_port(
            hmc,
            system_name_or_uuid,
            lpar_name_or_uuid,
            InventorySelector(adapter_id, physical_port_id, logical_port_id),
            Decimal(str(capacity_percent)),
            profile_name=profile_name,
            ownership_override=ownership_override,
        )

    return with_client(_go, profile=profile)


@tool(effect="mutate", operation="sriov.unassign_logical_port", target_kind="lpar")
def hmc_unassign_sriov_logical_port(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    profile_name: str,
    ownership_override: bool = False,
    profile: str | None = None,
) -> SriovLogicalPortChangeResult:
    """Unassign a profile logical port on a Not Activated LPAR.

    Args:
        system_name_or_uuid: Managed system name or UUID.
        lpar_name_or_uuid: Target partition name or UUID.
        profile_name: Exact partition profile to update and verify.
        adapter_id: Normalized SR-IOV adapter ID.
        physical_port_id: Normalized parent physical-port ID.
        logical_port_id: Normalized logical-port ID to remove.
        ownership_override: Permit a separately approved ADR 0011 ownership override.
        profile: TOML connection profile name.
    """

    async def _go(hmc):
        return await unassign_sriov_logical_port(
            hmc,
            system_name_or_uuid,
            lpar_name_or_uuid,
            InventorySelector(adapter_id, physical_port_id, logical_port_id),
            profile_name=profile_name,
            ownership_override=ownership_override,
        )

    return with_client(_go, profile=profile)
