"""MCP tools for virtual network and I/O adapters."""

from __future__ import annotations

from ..tool_registry import tool_module

from typing import Any

from .._app import run_sync, run_limited_collection
from ..client.client_adapters import AdapterType, validate_adapter_type
from ..client.client_factory import client_from_env
from ..operations.adapters import (
    add_network_adapter,
    add_vfc_adapter,
    add_vscsi_adapter,
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
    system_name_or_uuid: str | None = None,
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
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide.
    """
    validate_adapter_type(adapter_type)

    async def operation():
        async with client_from_env(profile) as hmc:
            return await list_adapters(
                hmc, system_name_or_uuid, lpar_name_or_uuid, adapter_type
            )

    return run_limited_collection(operation, limit)


@tool(effect="mutate", operation="adapter.add_network", target_kind="lpar")
def hmc_add_network_adapter(
    lpar_name_or_uuid: str,
    port_vlan_id: int,
    slot_number: int | None = None,
    virtual_switch_id: int | None = None,
    tagged: bool = False,
    mac_address: str | None = None,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
    ownership_override: bool = False,
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
        ownership_override: Bypass LPAR ownership rejection after operator approval.
        profile: TOML profile name, or the environment-default HMC when omitted.
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide.
    """

    async def operation():
        async with client_from_env(profile) as hmc:
            return (
                await add_network_adapter(
                    hmc,
                    system_name_or_uuid,
                    lpar_name_or_uuid,
                    port_vlan_id,
                    slot_number=slot_number,
                    virtual_switch_id=virtual_switch_id,
                    tagged=tagged,
                    mac_address=mac_address,
                    ownership_override=ownership_override,
                )
            ).resource

    return run_sync(operation)


# Not exhaustive: `vios_partition_id` is a slot number within one managed
# system, reused on every system in a fleet, so a `vios` allowlist entry can
# never name the VIOS this call actually pairs to. It has no UUID form to fall
# back on, so ADR 0039 grants this tool only under `targets = "all-targets"`.
@tool(
    effect="mutate",
    operation="adapter.add_vscsi",
    target_kind="lpar",
    exhaustive_targets=False,
)
def hmc_add_vscsi_adapter(
    lpar_name_or_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    slot_number: int | None = None,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
    ownership_override: bool = False,
) -> dict[str, Any] | None:
    """Add a virtual SCSI client adapter paired to a VIOS server slot.

    Args:
        lpar_name_or_uuid: Client partition name or UUID.
        vios_partition_id: Numeric VIOS partition ID from ``hmc_list_vios``.
        vios_slot: Server-side virtual slot configured on that VIOS.
        slot_number: Client virtual slot, or ``None`` for HMC auto-assignment.
        ownership_override: Bypass LPAR ownership rejection after operator approval.
        profile: TOML profile name, or the environment-default HMC when omitted.
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide.
    """

    async def operation():
        async with client_from_env(profile) as hmc:
            return (
                await add_vscsi_adapter(
                    hmc,
                    system_name_or_uuid,
                    lpar_name_or_uuid,
                    vios_partition_id,
                    vios_slot,
                    slot_number,
                    ownership_override=ownership_override,
                )
            ).resource

    return run_sync(operation)


# Not exhaustive: `vios_partition_id` is a slot number within one managed
# system, reused on every system in a fleet, so a `vios` allowlist entry can
# never name the VIOS this call actually pairs to. It has no UUID form to fall
# back on, so ADR 0039 grants this tool only under `targets = "all-targets"`.
@tool(
    effect="mutate",
    operation="adapter.add_vfc",
    target_kind="lpar",
    exhaustive_targets=False,
)
def hmc_add_vfc_adapter(
    lpar_name_or_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    slot_number: int | None = None,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
    ownership_override: bool = False,
) -> dict[str, Any] | None:
    """Add an NPIV virtual Fibre Channel client adapter to an LPAR.

    Args:
        lpar_name_or_uuid: Client partition name or UUID.
        vios_partition_id: Numeric VIOS partition ID from ``hmc_list_vios``.
        vios_slot: Server-side NPIV virtual slot configured on that VIOS.
        slot_number: Client virtual slot, or ``None`` for HMC auto-assignment.
        ownership_override: Bypass LPAR ownership rejection after operator approval.
        profile: TOML profile name, or the environment-default HMC when omitted.
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide.
    """

    async def operation():
        async with client_from_env(profile) as hmc:
            return (
                await add_vfc_adapter(
                    hmc,
                    system_name_or_uuid,
                    lpar_name_or_uuid,
                    vios_partition_id,
                    vios_slot,
                    slot_number,
                    ownership_override=ownership_override,
                )
            ).resource

    return run_sync(operation)


@tool(effect="destructive", operation="adapter.delete", target_kind="lpar")
def hmc_delete_adapter(
    lpar_name_or_uuid: str,
    adapter_type: AdapterType,
    adapter_uuid: str,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
    ownership_override: bool = False,
) -> str:
    """Remove an adapter by UUID, detaching its network or storage path.

    Confirm the adapter is no longer required before deletion.

    Args:
        lpar_name_or_uuid: Partition name or UUID containing the adapter.
        adapter_type: Adapter resource type that owns ``adapter_uuid``.
        adapter_uuid: Adapter UUID returned by ``hmc_list_adapters``.
        ownership_override: Bypass LPAR ownership rejection after operator approval.
        profile: TOML profile name, or the environment-default HMC when omitted.
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide.
    """
    validate_adapter_type(adapter_type)

    async def operation():
        async with client_from_env(profile) as hmc:
            await delete_adapter(
                hmc,
                system_name_or_uuid,
                lpar_name_or_uuid,
                adapter_type,
                adapter_uuid,
                ownership_override=ownership_override,
            )
        return f"Deleted {adapter_type} {adapter_uuid} from {lpar_name_or_uuid}"

    return run_sync(operation)
