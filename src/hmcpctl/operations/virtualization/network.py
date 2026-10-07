"""Presentation-neutral virtual-network operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hmcpctl.client.core import HMCClient

from ...errors import HMCError
from ...resource_identity import ResourceNotFoundError, resolve_system_uuid
from ...xmlutil import leaf_text
from ..error_translation import translate_virtual_network_create_error


def require_vlan_id(argument: str, value: int) -> None:
    """Refuse a VLAN id outside IEEE 802.1Q's usable 1-4094 before any HMC call."""
    if isinstance(value, bool) or not 1 <= value <= 4094:
        raise ValueError(
            f"{argument} {value!r} must be a VLAN id from 1 to 4094 "
            "(IEEE 802.1Q reserves 0 and 4095)"
        )


@dataclass(frozen=True)
class VirtualNetworkResult:
    system_uuid: str
    resource: dict[str, Any] | None


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


async def _switch_uuid(hmc: HMCClient, system_uuid: str, switch_id: int) -> str:
    """UUID of the VirtualSwitch whose SwitchID is *switch_id*.

    V10R3 refuses a VirtualNetwork create without its AssociatedSwitch link (#1374).
    """
    switches = await hmc.list_virtual_switches(system_uuid)
    ids = [leaf_text(switch["Resource"].get("SwitchID")) for switch in switches]
    for switch, found in zip(switches, ids, strict=True):
        if found == str(switch_id):
            return switch["UUID"]
    known = ", ".join(str(found) for found in ids) or "none"
    raise ResourceNotFoundError(
        "VirtualSwitch",
        str(switch_id),
        f"no VirtualSwitch has SwitchID {switch_id} "
        f"(SwitchIDs on this system: {known})",
    )


async def create_virtual_network(
    hmc: HMCClient,
    system_name_or_uuid: str,
    name: str,
    vlan_id: int,
    virtual_switch_id: int,
    *,
    tagged: bool = False,
) -> VirtualNetworkResult:
    """Create a virtual network on a managed system.

    Raises:
        ResourceNotFoundError: If the managed-system selector cannot be resolved, or
            no VirtualSwitch carries ``virtual_switch_id``.
        HMCError: If the HMC rejects the create request, including an invalid VLAN.
        ValueError: If ``vlan_id`` is outside 1-4094.
    """
    require_vlan_id("vlan_id", vlan_id)
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    switch_uuid = await _switch_uuid(hmc, system_uuid, virtual_switch_id)
    try:
        resource = await hmc.create_virtual_network(
            system_uuid,
            name,
            vlan_id,
            virtual_switch_id,
            switch_uuid=switch_uuid,
            tagged=tagged,
        )
    except HMCError as exc:
        translated = translate_virtual_network_create_error(exc)
        if translated is exc:
            raise
        raise translated from exc
    return VirtualNetworkResult(system_uuid, resource)


async def delete_virtual_network(
    hmc: HMCClient, system_name_or_uuid: str, network_uuid: str
) -> str:
    """Delete a virtual network and return its UUID.

    Raises:
        ResourceNotFoundError: If the managed-system selector cannot be resolved.
        HMCError: If the HMC rejects the delete request or it cannot be completed.
    """
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    await hmc.delete_virtual_network(system_uuid, network_uuid)
    return network_uuid


async def list_network_bridges(
    hmc: HMCClient, system_name_or_uuid: str
) -> list[dict[str, Any]]:
    """List network bridges on a managed system."""
    system_uuid = await resolve_system_uuid(hmc, system_name_or_uuid)
    return await hmc.list_network_bridges(system_uuid)
