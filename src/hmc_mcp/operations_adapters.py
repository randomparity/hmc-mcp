"""Presentation-neutral virtual-adapter operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import HMCClient
from .client_adapters import AdapterType, validate_adapter_type
from .resource_identity import resolve_lpar_uuid


@dataclass(frozen=True)
class AdapterResult:
    lpar_uuid: str
    resource: dict[str, Any] | None


async def list_adapters(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    adapter_type: AdapterType,
    system_name_or_uuid: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    validate_adapter_type(adapter_type)
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    return lpar_uuid, await hmc.list_adapters(lpar_uuid, adapter_type)


async def add_network_adapter(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    vlan: int,
    slot: int | None,
    vswitch: int | None,
    tagged: bool,
    mac: str | None,
    system_name_or_uuid: str | None = None,
) -> AdapterResult:
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    resource = await hmc.add_network_adapter(
        lpar_uuid, vlan, slot, vswitch, tagged, mac
    )
    return AdapterResult(lpar_uuid, resource)


async def add_vios_adapter(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    slot: int | None,
    *,
    fibre_channel: bool,
    system_name_or_uuid: str | None = None,
) -> AdapterResult:
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    add = hmc.add_vfc_adapter if fibre_channel else hmc.add_vscsi_adapter
    resource = await add(lpar_uuid, vios_partition_id, vios_slot, slot)
    return AdapterResult(lpar_uuid, resource)


async def delete_adapter(
    hmc: HMCClient,
    lpar_name_or_uuid: str,
    adapter_type: AdapterType,
    adapter_uuid: str,
    system_name_or_uuid: str | None = None,
) -> str:
    validate_adapter_type(adapter_type)
    lpar_uuid = await resolve_lpar_uuid(
        hmc, lpar_name_or_uuid, system_name_or_uuid=system_name_or_uuid
    )
    await hmc.delete_adapter(lpar_uuid, adapter_type, adapter_uuid)
    return lpar_uuid
