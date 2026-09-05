"""MCP tools for virtual networks, switches, and bridges."""

from __future__ import annotations

from typing import Any

from .._app import (
    run_limited_collection,
    with_client,
)
from ..operations.network import (
    create_virtual_network,
    delete_virtual_network,
    list_network_bridges,
    list_virtual_networks,
    list_virtual_switches,
)
from ..tool_registry import tool_module

tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="network.list_switches", target_kind="managed_system")
def hmc_list_virtual_switches(
    system_name_or_uuid: str,
    profile: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """List VirtualSwitches on a managed system (names, SwitchIDs, mode).

    The SwitchID is what hmc_create_virtual_network and hmc_add_network_adapter
    reference.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        profile: TOML profile name, or the environment-default HMC when omitted.
        limit: Maximum entries returned after the complete HMC feed is transferred
            and parsed; omitted returns all entries. This client-side cap does not
            reduce HMC work or network transfer.
    """

    return run_limited_collection(
        lambda hmc: list_virtual_switches(hmc, system_name_or_uuid),
        limit,
        profile=profile,
    )


@tool(effect="read", operation="network.list_networks", target_kind="managed_system")
def hmc_list_virtual_networks(
    system_name_or_uuid: str,
    profile: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """List Virtual Networks (VLANs) on a managed system.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        profile: TOML profile name, or the environment-default HMC when omitted.
        limit: Maximum entries returned after the complete HMC feed is transferred
            and parsed; omitted returns all entries. This client-side cap does not
            reduce HMC work or network transfer.
    """

    return run_limited_collection(
        lambda hmc: list_virtual_networks(hmc, system_name_or_uuid),
        limit,
        profile=profile,
    )


@tool(effect="mutate", operation="network.create_network", target_kind="managed_system")
def hmc_create_virtual_network(
    system_name_or_uuid: str,
    name: str,
    vlan_id: int,
    virtual_switch_id: int,
    tagged: bool = False,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Create a Virtual Network (VLAN) on a managed system.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        name: Unique virtual-network name on the managed system.
        vlan_id: VLAN identifier for the network.
        virtual_switch_id: Numeric SwitchID from ``hmc_list_virtual_switches``.
        tagged: Whether bridged traffic retains its VLAN tag.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def operation(hmc):
        result = await create_virtual_network(
            hmc,
            system_name_or_uuid,
            name,
            vlan_id,
            virtual_switch_id,
            tagged=tagged,
        )
        return result.resource

    return with_client(operation, profile=profile)


@tool(
    effect="destructive",
    operation="network.delete_network",
    target_kind="managed_system",
)
def hmc_delete_virtual_network(
    system_name_or_uuid: str, network_uuid: str, profile: str | None = None
) -> str:
    """Delete a Virtual Network from a managed system.

    Note: a network referenced by a NetworkBridge, or equal to a trunk
    adapter's PVID, cannot be deleted until the bridge is removed. Returns a
    confirmation string (immediate delete — no job to poll).

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        network_uuid: Virtual-network UUID from ``hmc_list_virtual_networks``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go(hmc):
        await delete_virtual_network(hmc, system_name_or_uuid, network_uuid)
        return f"Deleted VirtualNetwork {network_uuid} from {system_name_or_uuid}"

    return with_client(_go, profile=profile)


@tool(effect="read", operation="network.list_bridges", target_kind="managed_system")
def hmc_list_network_bridges(
    system_name_or_uuid: str,
    profile: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """List NetworkBridges (Shared Ethernet Adapters) on a managed system.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        profile: TOML profile name, or the environment-default HMC when omitted.
        limit: Maximum entries returned after the complete HMC feed is transferred
            and parsed; omitted returns all entries. This client-side cap does not
            reduce HMC work or network transfer.
    """

    return run_limited_collection(
        lambda hmc: list_network_bridges(hmc, system_name_or_uuid),
        limit,
        profile=profile,
    )
