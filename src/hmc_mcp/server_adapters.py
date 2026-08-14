"""MCP tools for virtual network and I/O adapters."""

from __future__ import annotations

from typing import Any

from ._app import _DESTRUCTIVE, _READ_ONLY, _run, mcp
from .client_adapters import AdapterType, validate_adapter_type
from .common import client_from_env
from .operations_adapters import (
    add_network_adapter,
    add_vios_adapter,
    delete_adapter,
    list_adapters,
)


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_adapters(
    lpar_name_or_uuid: str,
    adapter_type: AdapterType = "ClientNetworkAdapter",
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List one LPAR's virtual adapters of the selected adapter type."""
    validate_adapter_type(adapter_type)
    async def operation():
        async with client_from_env(profile) as hmc:
            _, adapters = await list_adapters(hmc, lpar_name_or_uuid, adapter_type)
            return adapters

    return _run(operation)


@mcp.tool
def hmc_add_network_adapter(
    lpar_name_or_uuid: str,
    port_vlan_id: int,
    slot_number: int | None = None,
    virtual_switch_id: int | None = None,
    tagged: bool = False,
    mac_address: str | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Add a virtual Ethernet adapter to an LPAR; active LPARs require RMC."""

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


@mcp.tool
def hmc_add_vscsi_adapter(
    lpar_name_or_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    slot_number: int | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Add a virtual SCSI client adapter paired to a VIOS server slot."""

    async def operation():
        async with client_from_env(profile) as hmc:
            return (
                await add_vios_adapter(
                    hmc, lpar_name_or_uuid, vios_partition_id, vios_slot, slot_number,
                    fibre_channel=False,
                )
            ).resource

    return _run(operation)


@mcp.tool
def hmc_add_vfc_adapter(
    lpar_name_or_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    slot_number: int | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Add an NPIV virtual Fibre Channel client adapter to an LPAR."""

    async def operation():
        async with client_from_env(profile) as hmc:
            return (
                await add_vios_adapter(
                    hmc, lpar_name_or_uuid, vios_partition_id, vios_slot, slot_number,
                    fibre_channel=True,
                )
            ).resource

    return _run(operation)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_delete_adapter(
    lpar_name_or_uuid: str,
    adapter_type: AdapterType,
    adapter_uuid: str,
    profile: str | None = None,
) -> str:
    """Remove an adapter by UUID, detaching its network or storage path."""
    validate_adapter_type(adapter_type)
    async def operation():
        async with client_from_env(profile) as hmc:
            await delete_adapter(hmc, lpar_name_or_uuid, adapter_type, adapter_uuid)
        return f"Deleted {adapter_type} {adapter_uuid} from {lpar_name_or_uuid}"

    return _run(operation)
