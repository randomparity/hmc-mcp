"""Presentation-neutral virtual-adapter operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hmc_mcp.client.core import HMCClient
from ..client.client_adapters import AdapterType, validate_adapter_type
from ..resource_identity import resolve_lpar_uuid
from hmc_mcp.operations.ownership import resolve_and_authorize_lpar_mutation


@dataclass(frozen=True)
class AdapterResult:
    lpar_uuid: str
    resource: dict[str, Any] | None


async def list_adapters(
    hmc: HMCClient,
    system_name_or_uuid: str | None,
    lpar_name_or_uuid: str,
    adapter_type: AdapterType,
) -> list[dict[str, Any]]:
    """List one kind of virtual adapter on an LPAR.

    Raises:
        ValueError: If ``adapter_type`` is unsupported.
    """
    validate_adapter_type(adapter_type)
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    return await hmc.list_adapters(lpar_uuid, adapter_type)


async def add_network_adapter(
    hmc: HMCClient,
    system_name_or_uuid: str | None,
    lpar_name_or_uuid: str,
    port_vlan_id: int,
    *,
    slot_number: int | None = None,
    virtual_switch_id: int | None = None,
    tagged: bool = False,
    mac_address: str | None = None,
    ownership_override: bool = False,
) -> AdapterResult:
    """Authorize the LPAR and add a virtual Ethernet adapter."""
    lpar_uuid = await resolve_and_authorize_lpar_mutation(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    resource = await hmc.add_network_adapter(
        lpar_uuid,
        port_vlan_id,
        slot_number,
        virtual_switch_id,
        tagged,
        mac_address,
    )
    return AdapterResult(lpar_uuid, resource)


async def add_vscsi_adapter(
    hmc: HMCClient,
    system_name_or_uuid: str | None,
    lpar_name_or_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    *,
    slot_number: int | None = None,
    ownership_override: bool = False,
) -> AdapterResult:
    """Authorize the LPAR and add a virtual SCSI adapter."""
    lpar_uuid = await resolve_and_authorize_lpar_mutation(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    resource = await hmc.add_vscsi_adapter(
        lpar_uuid, vios_partition_id, vios_slot, slot_number
    )
    return AdapterResult(lpar_uuid, resource)


async def add_vfc_adapter(
    hmc: HMCClient,
    system_name_or_uuid: str | None,
    lpar_name_or_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    *,
    slot_number: int | None = None,
    ownership_override: bool = False,
) -> AdapterResult:
    """Authorize the LPAR and add a virtual Fibre Channel adapter."""
    lpar_uuid = await resolve_and_authorize_lpar_mutation(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    resource = await hmc.add_vfc_adapter(
        lpar_uuid, vios_partition_id, vios_slot, slot_number
    )
    return AdapterResult(lpar_uuid, resource)


async def delete_adapter(
    hmc: HMCClient,
    system_name_or_uuid: str | None,
    lpar_name_or_uuid: str,
    adapter_type: AdapterType,
    adapter_uuid: str,
    *,
    ownership_override: bool = False,
) -> str:
    """Authorize the LPAR and delete one virtual adapter.

    Raises:
        ValueError: If ``adapter_type`` is unsupported.
    """
    validate_adapter_type(adapter_type)
    lpar_uuid = await resolve_and_authorize_lpar_mutation(
        hmc,
        system_name_or_uuid,
        lpar_name_or_uuid,
        ownership_override=ownership_override,
    )
    await hmc.delete_adapter(lpar_uuid, adapter_type, adapter_uuid)
    return adapter_uuid
