"""Presentation-neutral virtual-network operations."""

from __future__ import annotations

from typing import Any

from ..client import HMCClient
from ..resource_identity import resolve_system_uuid
from .error_translation import translate_virtual_network_create_error
from ..errors import HMCError


async def list_virtual_switches(
    hmc: HMCClient, system_name_or_uuid: str
) -> list[dict[str, Any]]:
    """List virtual switches on a managed system."""
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    return await hmc.list_virtual_switches(system_uuid)


async def list_virtual_networks(
    hmc: HMCClient, system_name_or_uuid: str
) -> list[dict[str, Any]]:
    """List virtual networks on a managed system."""
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    return await hmc.list_virtual_networks(system_uuid)


async def create_virtual_network(
    hmc: HMCClient,
    system_name_or_uuid: str,
    name: str,
    vlan_id: int,
    virtual_switch_id: int,
    *,
    tagged: bool = False,
) -> dict[str, Any] | None:
    """Create a virtual network on a managed system.

    Raises:
        ValueError: If the HMC reports an invalid VLAN or switch selection.
    """
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    try:
        return await hmc.create_virtual_network(
            system_uuid, name, vlan_id, virtual_switch_id, tagged=tagged
        )
    except HMCError as exc:
        translate_virtual_network_create_error(exc)
        raise


async def delete_virtual_network(
    hmc: HMCClient, system_name_or_uuid: str, network_uuid: str
) -> str:
    """Delete a virtual network and return its UUID."""
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    await hmc.delete_virtual_network(system_uuid, network_uuid)
    return network_uuid


async def list_network_bridges(
    hmc: HMCClient, system_name_or_uuid: str
) -> list[dict[str, Any]]:
    """List network bridges on a managed system."""
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    return await hmc.list_network_bridges(system_uuid)
