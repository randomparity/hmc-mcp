"""MCP tools for virtual networks, FC/SEA adapters, SR-IOV mode, and vNICs.
"""

from __future__ import annotations

from typing import Any, Literal

from ._app import (
    _DESTRUCTIVE,
    _READ_ONLY,
    _resolve_system_uuid,
    _run,
    _ssh_with_client,
    mcp,
)

from .common import client_from_env
from .ssh import (
    add_vnic,
    list_fc_ports,
    list_sea_adapters,
    list_vnics,
    remove_vnic,
    set_sriov_adapter_mode,
)



@mcp.tool(annotations=_READ_ONLY)
def hmc_list_virtual_switches(system_name_or_uuid: str) -> list[dict[str, Any]]:
    """List VirtualSwitches on a managed system (names, SwitchIDs, mode).

    system_name_or_uuid: accepts either a SystemName or a UUID
    (find it with hmc_systems).
    The SwitchID is what hmc_create_virtual_network and hmc_add_network_adapter
    reference.
    """

    async def _go():
        async with client_from_env() as hmc:
            system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
            return await hmc.list_virtual_switches(system_uuid)

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_virtual_networks(system_name_or_uuid: str) -> list[dict[str, Any]]:
    """List Virtual Networks (VLANs) on a managed system.

    system_name_or_uuid: accepts either a SystemName or a UUID
    (find it with hmc_systems).
    """

    async def _go():
        async with client_from_env() as hmc:
            system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
            return await hmc.list_virtual_networks(system_uuid)

    return _run(_go)


@mcp.tool
def hmc_create_virtual_network(
    system_name_or_uuid: str,
    name: str,
    vlan_id: int,
    vswitch_id: int,
    tagged: bool = False,
) -> dict[str, Any] | None:
    """Create a Virtual Network (VLAN) on a managed system.

    system_name_or_uuid: accepts either a SystemName or a UUID
    (find it with hmc_systems).
    vswitch_id is the numeric SwitchID of the backing VirtualSwitch (see
    hmc_list_virtual_switches). tagged sets whether bridged traffic keeps the
    VLAN tag.
    """

    async def _go():
        async with client_from_env() as hmc:
            system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
            return await hmc.create_virtual_network(
                system_uuid, name, vlan_id, vswitch_id, tagged=tagged
            )

    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_delete_virtual_network(system_name_or_uuid: str, network_uuid: str) -> str:
    """Delete a Virtual Network from a managed system.

    system_name_or_uuid: accepts either a SystemName or a UUID
    (find it with hmc_systems).
    Note: a network referenced by a NetworkBridge, or equal to a trunk
    adapter's PVID, cannot be deleted until the bridge is removed. Returns a
    confirmation string (immediate delete — no job to poll).
    """

    async def _go():
        async with client_from_env() as hmc:
            system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
            await hmc.delete_virtual_network(system_uuid, network_uuid)
        return f"Deleted VirtualNetwork {network_uuid} from {system_name_or_uuid}"

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_network_bridges(system_name_or_uuid: str) -> list[dict[str, Any]]:
    """List NetworkBridges (Shared Ethernet Adapters) on a managed system.

    system_name_or_uuid: accepts either a SystemName or a UUID
    (find it with hmc_systems).
    """

    async def _go():
        async with client_from_env() as hmc:
            system_uuid = await _resolve_system_uuid(hmc, system_name_or_uuid)
            return await hmc.list_network_bridges(system_uuid)

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_fc_ports(system_name_or_uuid: str, lpar_name_or_uuid: str | None = None) -> list[dict[str, str]]:
    """List Virtual Fibre Channel (NPIV) adapters for a managed system via the HMC CLI.

    Runs ``lshwres -r virtualio --rsubtype fc --level lpar -m <system_name>``
    on the HMC via SSH and returns parsed dicts with fields including
    lpar_name, slot_num, wwpns, and remote_lpar_id.

    The system and partition may be given by CLI name or by UUID; UUIDs
    are resolved to their CLI names via REST (falling back to an lssyscfg
    lookup over SSH when the REST API is unreachable) before the command
    runs. Pass lpar_name_or_uuid to restrict results to a single partition.
    Either a CLI name or UUID works; use hmc_systems to find a system
    UUID and hmc_lpars to find an LPAR UUID.    """
    return _ssh_with_client(
        lambda config, system_name, lpar_name: list_fc_ports(config, system_name, lpar_name),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
    )


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_sea_adapters(system_name_or_uuid: str, lpar_name_or_uuid: str | None = None) -> list[dict[str, str]]:
    """List Shared Ethernet Adapter (SEA) virtual Ethernet ports via the HMC CLI.

    Runs ``lshwres -r virtualio --rsubtype eth --level lpar -m <system_name>
    -F lpar_name,port_vlan_id,vswitch,state,trunk_priority`` on the HMC via
    SSH and returns parsed dicts with those five fields.

    The system and partition may be given by CLI name or by UUID; UUIDs
    are resolved to their CLI names via REST (falling back to an lssyscfg
    lookup over SSH when the REST API is unreachable) before the command
    runs. Pass lpar_name_or_uuid to restrict results to a single partition.
    Either a CLI name or UUID works; use hmc_systems to find a system
    UUID and hmc_lpars to find an LPAR UUID.    """
    return _ssh_with_client(
        lambda config, system_name, lpar_name: list_sea_adapters(config, system_name, lpar_name),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
    )



@mcp.tool
def hmc_set_sriov_adapter_mode(
    system_name_or_uuid: str,
    adapter_id: str,
    mode: Literal["sriov", "dedicated"],
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
    functions on that adapter. Confirm system_name_or_uuid and adapter_id before calling.    """
    return _ssh_with_client(
        lambda config, system_name, _: set_sriov_adapter_mode(
            config, system_name, adapter_id, mode
        ),
        system_name_or_uuid=system_name_or_uuid,
    )




@mcp.tool(annotations=_READ_ONLY)
def hmc_list_vnics(system_name_or_uuid: str, lpar_name_or_uuid: str) -> list[dict[str, Any]]:
    """List vNICs (SR-IOV-backed Virtual NICs) on an LPAR via the HMC CLI.

    Runs ``lshwres -r virtualio --rsubtype vnic --level lpar -m <system_name>
    --filter lpar_names=<lpar_name>`` on the HMC via SSH and returns one dict
    per vNIC with fields such as ``vnic_id``, ``capacity``, ``vswitch_name``,
    ``port_vlan_id``, and ``backing_devices``.

    The system and partition may be given by CLI name or by UUID; UUIDs
    are resolved to their CLI names via REST (falling back to an lssyscfg
    lookup over SSH when the REST API is unreachable) before the command
    runs. Use ``hmc_systems`` to find a system UUID and
    ``hmc_lpars`` to find an LPAR UUID.    """
    return _ssh_with_client(
        lambda config, system_name, lpar_name: list_vnics(config, system_name, lpar_name),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
    )


@mcp.tool
def hmc_add_vnic(
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    capacity: int,
    vswitch_name: str,
    port_vlan_id: int,
    backing_devices: str | None = None,
) -> str:
    """Add a vNIC (SR-IOV-backed Virtual NIC) to an LPAR via the HMC CLI.

    Runs ``chhwres -r virtualio --rsubtype vnic -o a -m <system_name>
    --filter lpar_names=<lpar_name> -a "<attrs>"`` on the HMC via SSH.

    The system and partition may be given by CLI name or by UUID; UUIDs
    are resolved to their CLI names via REST (falling back to an lssyscfg
    lookup over SSH when the REST API is unreachable) before the command
    runs.

    **V1 scope boundary:** Only the following parameters are supported in
    this version: ``capacity``, ``vswitch_name``, ``port_vlan_id``, and
    ``backing_devices`` (optional, opaque string passed verbatim).  Complex
    backing-device topology (multi-adapter failover, per-device SR-IOV
    physical port IDs, capacity weights) is a follow-up and explicitly out
    of scope for v1.

    Returns the raw HMC command output on success.

    WARNING: This modifies the LPAR configuration on the HMC. Confirm
    system_name_or_uuid, lpar_name_or_uuid, and vswitch_name before calling.  The
    underlying physical adapter must be in SR-IOV mode (see
    ``hmc_set_sriov_adapter_mode``).

    Raises:
        HMCCLIError: If the HMC command fails, e.g. because the underlying
            SR-IOV adapter is not in SR-IOV mode.
    """
    return _ssh_with_client(
        lambda config, system_name, lpar_name: add_vnic(
            config,
            system_name,
            lpar_name,
            capacity,
            vswitch_name,
            port_vlan_id,
            backing_devices,
        ),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
    )


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_remove_vnic(system_name_or_uuid: str, lpar_name_or_uuid: str, vnic_id: str) -> str:
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
    output (immediate delete — no job to poll).    """
    return _ssh_with_client(
        lambda config, system_name, lpar_name: remove_vnic(
            config, system_name, lpar_name, vnic_id
        ),
        system_name_or_uuid=system_name_or_uuid,
        lpar_name_or_uuid=lpar_name_or_uuid,
    )
