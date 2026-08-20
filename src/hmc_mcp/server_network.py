"""MCP tools for virtual networks, FC/SEA adapters, SR-IOV mode, and vNICs."""

from __future__ import annotations

from .tool_registry import tool_module

from typing import Any

from ._app import (
    _run,
    _run_limited_collection,
)

from .common import build_config, client_from_env
from .operations_network import (
    create_virtual_network,
    delete_virtual_network,
    list_network_bridges,
    list_virtual_networks,
    list_virtual_switches,
)
from .operations_ssh_network import (
    add_vnic,
    list_fc_ports,
    list_sea_adapters,
    list_vnics,
    remove_vnic,
)
from .operations_pcie import (
    SriovMode,
    assign_sriov_logical_port,
    set_sriov_adapter_mode,
    unassign_sriov_logical_port,
)


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

    async def _go():
        async with client_from_env(profile) as hmc:
            return await list_virtual_switches(hmc, system_name_or_uuid)

    return _run_limited_collection(_go, limit)


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

    async def _go():
        async with client_from_env(profile) as hmc:
            return await list_virtual_networks(hmc, system_name_or_uuid)

    return _run_limited_collection(_go, limit)


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

    async def _go():
        async with client_from_env(profile) as hmc:
            return await create_virtual_network(
                hmc,
                system_name_or_uuid,
                name,
                vlan_id,
                virtual_switch_id,
                tagged=tagged,
            )

    return _run(_go)


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

    async def _go():
        async with client_from_env(profile) as hmc:
            await delete_virtual_network(hmc, system_name_or_uuid, network_uuid)
        return f"Deleted VirtualNetwork {network_uuid} from {system_name_or_uuid}"

    return _run(_go)


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

    async def _go():
        async with client_from_env(profile) as hmc:
            return await list_network_bridges(hmc, system_name_or_uuid)

    return _run_limited_collection(_go, limit)


@tool(effect="read", operation="network.list_fc_ports", target_kind="managed_system")
def hmc_list_fc_ports(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str | None = None,
    profile: str | None = None,
) -> list[dict[str, str]]:
    """List Virtual Fibre Channel (NPIV) adapters for a managed system via the HMC CLI.

    Runs ``lshwres -r virtualio --rsubtype fc --level lpar -m <system_name>``
    on the HMC via SSH and returns parsed dicts with fields including
    lpar_name, slot_num, wwpns, and remote_lpar_id.

    The system and partition may be given by CLI name or by UUID; UUIDs
    are resolved to their CLI names via REST (falling back to an lssyscfg
    lookup over SSH when the REST API is unreachable) before the command
    runs. Pass lpar_name_or_uuid to restrict results to a single partition.
    Either a CLI name or UUID works; use hmc_list_systems to find a system
    UUID and hmc_list_lpars to find an LPAR UUID.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: Optional partition name or UUID to restrict results.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _run(
        lambda: list_fc_ports(
            build_config(profile=profile), system_name_or_uuid, lpar_name_or_uuid
        )
    )


@tool(effect="read", operation="network.list_sea", target_kind="managed_system")
def hmc_list_sea_adapters(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str | None = None,
    profile: str | None = None,
) -> list[dict[str, str]]:
    """List Shared Ethernet Adapter (SEA) virtual Ethernet ports via the HMC CLI.

    Runs ``lshwres -r virtualio --rsubtype eth --level lpar -m <system_name>
    -F lpar_name,port_vlan_id,vswitch,state,trunk_priority`` on the HMC via
    SSH and returns parsed dicts with those five fields.

    The system and partition may be given by CLI name or by UUID; UUIDs
    are resolved to their CLI names via REST (falling back to an lssyscfg
    lookup over SSH when the REST API is unreachable) before the command
    runs. Pass lpar_name_or_uuid to restrict results to a single partition.
    Either a CLI name or UUID works; use hmc_list_systems to find a system
    UUID and hmc_list_lpars to find an LPAR UUID.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: Optional partition name or UUID to restrict results.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _run(
        lambda: list_sea_adapters(
            build_config(profile=profile), system_name_or_uuid, lpar_name_or_uuid
        )
    )


@tool(effect="mutate", operation="sriov.set_mode", target_kind="managed_system")
def hmc_set_sriov_adapter_mode(
    system_name_or_uuid: str,
    adapter_id: str,
    mode: SriovMode,
    profile: str | None = None,
) -> str:
    """Toggle a physical SR-IOV adapter between SR-IOV and dedicated mode.

    Runs ``chhwres -r sriov -m <system_name> -o s --id <adapter_id>
    -a "sriov_adapter_mode=<mode>"`` on the HMC via SSH and returns the raw
    command output.

    The system may be given by CLI name or by UUID; a UUID is resolved to
    its CLI name via REST (falling back to an lssyscfg lookup over SSH when
    the REST API is unreachable) before the command runs.

    ``adapter_id`` is the physical adapter identifier as reported by
    ``hmc_list_io_slots``.

    ``mode`` must be one of:
      - ``"sriov"``      — enable SR-IOV mode (shared virtual functions)
      - ``"dedicated"``  — disable SR-IOV, use as a dedicated (passthrough) adapter

    WARNING: Changing SR-IOV adapter mode affects all partitions using virtual
    functions on that adapter. Confirm system_name_or_uuid and adapter_id before calling.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        adapter_id: Physical adapter ID returned by ``hmc_list_io_slots``.
        mode: ``sriov`` for shared virtual functions or ``dedicated`` for
            passthrough use.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _run(
        lambda: set_sriov_adapter_mode(
            build_config(profile=profile), system_name_or_uuid, adapter_id, mode
        )
    )


@tool(effect="mutate", operation="sriov.assign_logical_port", target_kind="lpar")
def hmc_assign_sriov_logical_port(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    capacity_percent: float,
    profile_name: str | None = None,
    ownership_override: bool = False,
    profile: str | None = None,
) -> dict[str, Any]:
    """Assign an evidence-backed Ethernet SR-IOV logical port.

    Args:
        system_name_or_uuid: Managed system name or UUID.
        lpar_name_or_uuid: Target partition name or UUID.
        adapter_id: Normalized SR-IOV adapter ID.
        physical_port_id: Normalized parent physical-port ID.
        logical_port_id: Normalized unconfigured logical-port ID.
        capacity_percent: Requested percentage capacity from 1 through 100.
        profile_name: Optional profile whose unchanged state is also verified.
        ownership_override: Permit a separately approved ADR 0011 ownership override.
        profile: TOML connection profile name.
    """
    from dataclasses import asdict
    from decimal import Decimal

    async def _go():
        async with client_from_env(profile) as hmc:
            return asdict(
                await assign_sriov_logical_port(
                    hmc,
                    system_name_or_uuid,
                    lpar_name_or_uuid,
                    adapter_id,
                    physical_port_id,
                    logical_port_id,
                    Decimal(str(capacity_percent)),
                    profile_name=profile_name,
                    ownership_override=ownership_override,
                )
            )

    return _run(_go)


@tool(effect="mutate", operation="sriov.unassign_logical_port", target_kind="lpar")
def hmc_unassign_sriov_logical_port(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    profile_name: str,
    adapter_id: str,
    physical_port_id: str,
    logical_port_id: str,
    ownership_override: bool = False,
    profile: str | None = None,
) -> dict[str, Any]:
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
    from dataclasses import asdict

    async def _go():
        async with client_from_env(profile) as hmc:
            return asdict(
                await unassign_sriov_logical_port(
                    hmc,
                    system_name_or_uuid,
                    lpar_name_or_uuid,
                    profile_name,
                    adapter_id,
                    physical_port_id,
                    logical_port_id,
                    ownership_override=ownership_override,
                )
            )

    return _run(_go)


@tool(effect="read", operation="vnic.list", target_kind="lpar")
def hmc_list_vnics(
    system_name_or_uuid: str, lpar_name_or_uuid: str, profile: str | None = None
) -> list[dict[str, Any]]:
    """List vNICs (SR-IOV-backed Virtual NICs) on an LPAR via the HMC CLI.

    Runs ``lshwres -r virtualio --rsubtype vnic --level lpar -m <system_name>
    --filter lpar_names=<lpar_name>`` on the HMC via SSH and returns one dict
    per vNIC with fields such as ``vnic_id``, ``capacity``, ``vswitch_name``,
    ``port_vlan_id``, and ``backing_devices``.

    The system and partition may be given by CLI name or by UUID; UUIDs
    are resolved to their CLI names via REST (falling back to an lssyscfg
    lookup over SSH when the REST API is unreachable) before the command
    runs. Use ``hmc_list_systems`` to find a system UUID and
    ``hmc_list_lpars`` to find an LPAR UUID.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: Partition name or UUID from ``hmc_list_lpars``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _run(
        lambda: list_vnics(
            build_config(profile=profile), system_name_or_uuid, lpar_name_or_uuid
        )
    )


@tool(effect="mutate", operation="vnic.add", target_kind="lpar")
def hmc_add_vnic(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    capacity: int,
    virtual_switch_name: str,
    port_vlan_id: int,
    backing_devices: str | None = None,
    profile: str | None = None,
) -> str:
    """Add a vNIC (SR-IOV-backed Virtual NIC) to an LPAR via the HMC CLI.

    Runs ``chhwres -r virtualio --rsubtype vnic -o a -m <system_name>
    --filter lpar_names=<lpar_name> -a "<attrs>"`` on the HMC via SSH.

    The system and partition may be given by CLI name or by UUID; UUIDs
    are resolved to their CLI names via REST (falling back to an lssyscfg
    lookup over SSH when the REST API is unreachable) before the command
    runs.

    **V1 scope boundary:** Only the following parameters are supported in
    this version: ``capacity``, ``virtual_switch_name``, ``port_vlan_id``, and
    ``backing_devices`` (optional, opaque string passed verbatim).  Complex
    backing-device topology (multi-adapter failover, per-device SR-IOV
    physical port IDs, capacity weights) is a follow-up and explicitly out
    of scope for v1.

    Returns the raw HMC command output on success.

    WARNING: This modifies the LPAR configuration on the HMC. Confirm
    system_name_or_uuid, lpar_name_or_uuid, and virtual_switch_name before calling.  The
    underlying physical adapter must be in SR-IOV mode (see
    ``hmc_set_sriov_adapter_mode``).

    Raises:
        HMCCLIError: If the HMC command fails, e.g. because the underlying
            SR-IOV adapter is not in SR-IOV mode.

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: Partition name or UUID from ``hmc_list_lpars``.
        capacity: Requested vNIC capacity percentage.
        virtual_switch_name: Backing switch name from ``hmc_list_virtual_switches``.
        port_vlan_id: Port VLAN ID assigned to untagged traffic.
        backing_devices: Optional opaque HMC backing-device attribute string.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _run(
        lambda: add_vnic(
            build_config(profile=profile),
            system_name_or_uuid,
            lpar_name_or_uuid,
            capacity,
            virtual_switch_name,
            port_vlan_id,
            backing_devices,
        )
    )


@tool(effect="destructive", operation="vnic.remove", target_kind="lpar")
def hmc_remove_vnic(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    vnic_id: str,
    profile: str | None = None,
) -> str:
    """Remove a vNIC from an LPAR via the HMC CLI.

    Runs ``chhwres -r virtualio --rsubtype vnic -o r -m <system_name>
    --filter lpar_names=<lpar_name> -a "vnic_id=<vnic_id>"`` on the HMC
    via SSH.

    The system and partition may be given by CLI name or by UUID; UUIDs
    are resolved to their CLI names via REST (falling back to an lssyscfg
    lookup over SSH when the REST API is unreachable) before the command
    runs.

    ``vnic_id`` is the numeric ID as reported by ``hmc_list_vnics``.

    WARNING: This modifies the LPAR configuration on the HMC. Confirm
    system_name_or_uuid, lpar_name_or_uuid, and vnic_id before calling. Returns the HMC CLI
    output (immediate delete — no job to poll).

    Args:
        system_name_or_uuid: System name or UUID from ``hmc_list_systems``.
        lpar_name_or_uuid: Partition name or UUID from ``hmc_list_lpars``.
        vnic_id: Numeric vNIC ID returned by ``hmc_list_vnics``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    return _run(
        lambda: remove_vnic(
            build_config(profile=profile),
            system_name_or_uuid,
            lpar_name_or_uuid,
            vnic_id,
        ),
    )
