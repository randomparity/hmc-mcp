"""MCP tools for virtual network and I/O adapters."""

from __future__ import annotations

from .tool_registry import tool_module

from typing import Any

from ._app import _run, _run_limited_collection
from .client_adapters import AdapterType, validate_adapter_type
from .common import client_from_env
from .operations_adapters import (
    add_network_adapter,
    add_vios_adapter,
    delete_adapter,
    list_adapters,
)


tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="adapter.list", target_kind="lpar")
def hmc_list_adapters(
    lpar_name_or_uuid: str,
    adapter_type: AdapterType = "ClientNetworkAdapter",
    profile: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """List one LPAR's virtual adapters of the selected adapter type.

    Args:
        lpar_name_or_uuid: Partition name or UUID; discover partitions with
            ``hmc_list_lpars``.
        adapter_type: Adapter resource type to list.
        profile: TOML profile name, or the environment-default HMC when omitted.
        limit: Maximum entries returned after the complete HMC feed is transferred
            and parsed; omitted returns all entries. This client-side cap does not
            reduce HMC work or network transfer.
    """
    validate_adapter_type(adapter_type)

    async def operation():
        async with client_from_env(profile) as hmc:
            _, adapters = await list_adapters(hmc, lpar_name_or_uuid, adapter_type)
            return adapters

    return _run_limited_collection(operation, limit)


@tool(effect="mutate", operation="adapter.add_network", target_kind="lpar")
def hmc_add_network_adapter(
    lpar_name_or_uuid: str,
    port_vlan_id: int,
    slot_number: int | None = None,
    virtual_switch_id: int | None = None,
    tagged: bool = False,
    mac_address: str | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Add a virtual Ethernet adapter to an LPAR; active LPARs require RMC.

    Args:
        lpar_name_or_uuid: Partition name or UUID; discover partitions with
            ``hmc_list_lpars``.
        port_vlan_id: Port VLAN ID assigned to untagged traffic.
        slot_number: Client virtual slot, or ``None`` for HMC auto-assignment.
        virtual_switch_id: Numeric switch ID from ``hmc_list_virtual_switches``,
            or ``None`` to use the HMC default.
        tagged: Whether the adapter accepts IEEE 802.1Q tagged VLAN traffic.
        mac_address: Optional 12-hex-digit MAC address, with no separators.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def operation():
        async with client_from_env(profile) as hmc:
            return (
                await add_network_adapter(
                    hmc,
                    lpar_name_or_uuid,
                    port_vlan_id,
                    slot_number,
                    virtual_switch_id,
                    tagged,
                    mac_address,
                )
            ).resource

    return _run(operation)


@tool(effect="mutate", operation="adapter.add_vscsi", target_kind="lpar")
def hmc_add_vscsi_adapter(
    lpar_name_or_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    slot_number: int | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Add a virtual SCSI client adapter paired to a VIOS server slot.

    Args:
        lpar_name_or_uuid: Client partition name or UUID.
        vios_partition_id: Numeric VIOS partition ID from ``hmc_list_vios``.
        vios_slot: Server-side virtual slot configured on that VIOS.
        slot_number: Client virtual slot, or ``None`` for HMC auto-assignment.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def operation():
        async with client_from_env(profile) as hmc:
            return (
                await add_vios_adapter(
                    hmc,
                    lpar_name_or_uuid,
                    vios_partition_id,
                    vios_slot,
                    slot_number,
                    fibre_channel=False,
                )
            ).resource

    return _run(operation)


@tool(effect="mutate", operation="adapter.add_vfc", target_kind="lpar")
def hmc_add_vfc_adapter(
    lpar_name_or_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    slot_number: int | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Add an NPIV virtual Fibre Channel client adapter to an LPAR.

    Args:
        lpar_name_or_uuid: Client partition name or UUID.
        vios_partition_id: Numeric VIOS partition ID from ``hmc_list_vios``.
        vios_slot: Server-side NPIV virtual slot configured on that VIOS.
        slot_number: Client virtual slot, or ``None`` for HMC auto-assignment.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def operation():
        async with client_from_env(profile) as hmc:
            return (
                await add_vios_adapter(
                    hmc,
                    lpar_name_or_uuid,
                    vios_partition_id,
                    vios_slot,
                    slot_number,
                    fibre_channel=True,
                )
            ).resource

    return _run(operation)


@tool(effect="destructive", operation="adapter.delete", target_kind="lpar")
def hmc_delete_adapter(
    lpar_name_or_uuid: str,
    adapter_type: AdapterType,
    adapter_uuid: str,
    profile: str | None = None,
) -> str:
    """Remove an adapter by UUID, detaching its network or storage path.

    Confirm the adapter is no longer required before deletion.

    Args:
        lpar_name_or_uuid: Partition name or UUID containing the adapter.
        adapter_type: Adapter resource type that owns ``adapter_uuid``.
        adapter_uuid: Adapter UUID returned by ``hmc_list_adapters``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    validate_adapter_type(adapter_type)

    async def operation():
        async with client_from_env(profile) as hmc:
            await delete_adapter(hmc, lpar_name_or_uuid, adapter_type, adapter_uuid)
        return f"Deleted {adapter_type} {adapter_uuid} from {lpar_name_or_uuid}"

    return _run(operation)
