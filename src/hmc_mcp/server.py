"""FastMCP server exposing the IBM HMC REST API as MCP tools.

Run:
    hmc-mcp serve            # stdio transport (default, for agents)
    hmc-mcp serve --http     # streamable HTTP on 127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import FastMCP

from .client import HMCClient
from .common import client_from_env
from .jobs import power_off_lpar_job, power_on_lpar_job
from .templates import PARTITION_TYPES, build_lpar_document

mcp = FastMCP(
    name="hmc-mcp",
    instructions=(
        "Tools for querying and operating IBM Power systems via the HMC "
        "(Hardware Management Console) REST API. Managed systems are Power "
        "servers; logical partitions (LPARs) are the AIX/Linux/IBM i virtual "
        "servers running on them. Most read tools return parsed uom Atom "
        "entries as JSON."
    ),
)


def _run(coro):
    """Run an async client call from a sync tool function."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------- #
# Read-only tools
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_console_info() -> dict[str, Any] | None:
    """Get HMC version, network configuration and links to managed systems.

    Useful as a connectivity check — this is the cheapest HMC call.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_console_info()

    return _run(_go())


@mcp.tool
def hmc_list_systems() -> list[dict[str, Any]]:
    """List all managed systems (Power servers) known to the HMC.

    Returns one entry per system with UUID, SystemName, State, MTMS
    (machine type/model/serial), IPAddress, etc.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_managed_systems()

    return _run(_go())


@mcp.tool
def hmc_get_system(system_uuid: str) -> dict[str, Any] | None:
    """Get full details for one managed system by UUID."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_managed_system(system_uuid)

    return _run(_go())


@mcp.tool
def hmc_list_lpars(system_uuid: str | None = None) -> list[dict[str, Any]]:
    """List logical partitions (LPARs).

    If system_uuid is omitted, lists every LPAR known to the HMC; otherwise
    only the LPARs of that managed system.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_logical_partitions(system_uuid)

    return _run(_go())


@mcp.tool
def hmc_get_lpar(lpar_uuid: str) -> dict[str, Any] | None:
    """Get full details for one logical partition by UUID."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_logical_partition(lpar_uuid)

    return _run(_go())


@mcp.tool
def hmc_find_lpar(name: str) -> dict[str, Any] | None:
    """Find a logical partition by its partition name (exact match)."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.find_partition_by_name(name)

    return _run(_go())


@mcp.tool
def hmc_lpar_state(lpar_uuid: str) -> Any:
    """Get just the current state of an LPAR (running, not activated, ...).

    Uses the cheap quick-property endpoint instead of a full fetch.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_quick_property("LogicalPartition", lpar_uuid, "PartitionState")

    return _run(_go())


@mcp.tool
def hmc_list_vios(system_uuid: str | None = None) -> list[dict[str, Any]]:
    """List Virtual I/O Servers, optionally restricted to one managed system."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_vios(system_uuid)

    return _run(_go())


@mcp.tool
def hmc_list_resources(resource_type: str) -> list[dict[str, Any]]:
    """List any uom resource type exposed by the HMC.

    Examples: ManagedSystem, LogicalPartition, VirtualIOServer,
    LogicalPartitionProfile, VirtualSwitch, VirtualNetwork, SharedMemoryPool,
    SharedProcessorPool, HostEthernetAdapter, SRIOVAdapter, Cluster.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_uom(resource_type)

    return _run(_go())


# ---------------------------------------------------------------------- #
# Mutating tools (jobs)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_power_on_lpar(lpar_uuid: str) -> dict[str, Any] | None:
    """Submit a PowerOn job for a logical partition.

    Returns the submitted job (check hmc_get_job for status). This changes
    the state of a real partition — confirm the UUID with hmc_find_lpar
    before calling.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.submit_job(
                f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/PowerOn",
                power_on_lpar_job(),
            )

    return _run(_go())


@mcp.tool
def hmc_power_off_lpar(lpar_uuid: str, immediate: bool = False) -> dict[str, Any] | None:
    """Submit a PowerOff job for a logical partition.

    immediate=True forces an immediate power off (no graceful OS shutdown).
    Returns the submitted job. This changes the state of a real partition.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.submit_job(
                f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/PowerOff",
                power_off_lpar_job(immediate=immediate),
            )

    return _run(_go())


@mcp.tool
def hmc_get_job(job_uuid: str) -> dict[str, Any] | None:
    """Get the status/result of an HMC job by UUID."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_job(job_uuid)

    return _run(_go())


# ---------------------------------------------------------------------- #
# LPAR lifecycle (create / modify / delete)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_create_lpar(
    system_uuid: str,
    name: str,
    partition_type: str = "AIX/Linux",
    partition_id: int | None = None,
    min_memory: int = 256,
    desired_memory: int = 4096,
    max_memory: int = 8192,
    dedicated: bool = False,
    min_procs: float | None = None,
    desired_procs: float | None = None,
    max_procs: float | None = None,
    min_vcpus: int | None = None,
    desired_vcpus: int | None = 1,
    max_vcpus: int | None = 2,
    uncapped: bool = True,
) -> dict[str, Any] | None:
    """Create a new LPAR on a managed system.

    system_uuid is the target managed system (find it with hmc_list_systems).
    Memory values are in MiB. By default a shared-processor partition is
    created; set dedicated=True for dedicated CPUs (then procs are whole CPU
    counts). For shared partitions, procs are processing units (may be
    fractional, e.g. 0.5) and vcpus are virtual processor counts.

    The partition is created powered off with a default profile; storage,
    network and boot settings still need to be configured (via the HMC UI or
    profile edits) before it can boot an OS. This creates a real partition —
    confirm name/system_uuid before calling.

    partition_type must be one of: 'AIX/Linux', 'OS400', 'Virtual IO Server'.
    """
    xml = build_lpar_document(
        name=name,
        partition_type=partition_type,
        partition_id=partition_id,
        min_memory=min_memory,
        desired_memory=desired_memory,
        max_memory=max_memory,
        dedicated=dedicated,
        min_procs=min_procs,
        desired_procs=desired_procs,
        max_procs=max_procs,
        min_vcpus=min_vcpus,
        desired_vcpus=desired_vcpus,
        max_vcpus=max_vcpus,
        uncapped=uncapped,
    )

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.create_logical_partition(system_uuid, xml)

    return _run(_go())


@mcp.tool
def hmc_modify_lpar(
    lpar_uuid: str,
    name: str | None = None,
    min_memory: int | None = None,
    desired_memory: int | None = None,
    max_memory: int | None = None,
    dedicated: bool = False,
    min_procs: float | None = None,
    desired_procs: float | None = None,
    max_procs: float | None = None,
    min_vcpus: int | None = None,
    desired_vcpus: int | None = None,
    max_vcpus: int | None = None,
    uncapped: bool = True,
) -> dict[str, Any] | None:
    """Modify an LPAR's name and/or resource assignment (memory / CPU).

    Only the fields you pass are changed. Memory values are in MiB. For a
    running partition these are dynamic (DLPAR) operations and require an
    active RMC connection; otherwise the change applies on next activation.
    Set dedicated=True to assign whole CPUs, False (default) for shared
    processing units + virtual processors.
    """
    xml = build_lpar_document(
        name=name,
        min_memory=min_memory,
        desired_memory=desired_memory,
        max_memory=max_memory,
        dedicated=dedicated,
        min_procs=min_procs,
        desired_procs=desired_procs,
        max_procs=max_procs,
        min_vcpus=min_vcpus,
        desired_vcpus=desired_vcpus,
        max_vcpus=max_vcpus,
        uncapped=uncapped,
    )

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.modify_logical_partition(lpar_uuid, xml)

    return _run(_go())


@mcp.tool
def hmc_delete_lpar(lpar_uuid: str) -> str:
    """Delete (destroy) an LPAR by UUID.

    The partition must be powered off first (use hmc_power_off_lpar and
    confirm with hmc_lpar_state). This permanently removes the partition and
    its profiles from the HMC — it is irreversible. Confirm the UUID with
    hmc_find_lpar before calling.
    """

    async def _go():
        async with client_from_env() as hmc:
            await hmc.delete_logical_partition(lpar_uuid)
            return f"Deleted LPAR {lpar_uuid}"

    return _run(_go())


# ---------------------------------------------------------------------- #
# Virtual adapters (storage / network)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_list_adapters(lpar_uuid: str, adapter_type: str = "ClientNetworkAdapter") -> list[dict[str, Any]]:
    """List an LPAR's virtual adapters of a given type.

    adapter_type is one of: ClientNetworkAdapter, VirtualSCSIClientAdapter,
    VirtualFibreChannelClientAdapter, VirtualNICDedicated.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_child("LogicalPartition", lpar_uuid, adapter_type)

    return _run(_go())


@mcp.tool
def hmc_add_network_adapter(
    lpar_uuid: str,
    port_vlan_id: int,
    slot_number: int | None = None,
    virtual_switch_id: int | None = None,
    tagged: bool = False,
    mac_address: str | None = None,
) -> dict[str, Any] | None:
    """Add a Virtual Ethernet (client network) adapter to an LPAR.

    port_vlan_id is the PVID / VLAN the adapter connects to (this is how the
    adapter is attached to a VirtualNetwork — match the network's VLAN ID and
    vSwitch). slot_number is the virtual slot (auto-assigned if omitted).
    tagged=True makes it a VLAN-tagged (trunking) adapter; mac_address pins
    the MAC (otherwise the HMC generates one). Modifying a running LPAR is a
    DLPAR operation and needs active RMC.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.add_network_adapter(
                lpar_uuid, port_vlan_id, slot_number, virtual_switch_id, tagged, mac_address
            )

    return _run(_go())


@mcp.tool
def hmc_add_vscsi_adapter(
    lpar_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    slot_number: int | None = None,
) -> dict[str, Any] | None:
    """Add a Virtual SCSI client adapter to an LPAR, paired to a VIOS.

    vios_partition_id is the integer PartitionID of the serving VIOS (find it
    with hmc_list_vios), and vios_slot is that VIOS's server-side virtual
    SCSI slot number that owns the backing storage. slot_number is the client
    adapter's virtual slot (auto-assigned if omitted). Storage backing devices
    (disks / logical volumes) are then mapped to this adapter on the VIOS.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.add_vscsi_adapter(lpar_uuid, vios_partition_id, vios_slot, slot_number)

    return _run(_go())


@mcp.tool
def hmc_add_vfc_adapter(
    lpar_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    slot_number: int | None = None,
) -> dict[str, Any] | None:
    """Add a Virtual Fibre Channel (NPIV) client adapter to an LPAR.

    vios_partition_id is the integer PartitionID of the serving VIOS and
    vios_slot is its server-side virtual FC slot number. The HMC generates the
    WWPNs. Use this for SAN storage via NPIV instead of vSCSI.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.add_vfc_adapter(lpar_uuid, vios_partition_id, vios_slot, slot_number)

    return _run(_go())


@mcp.tool
def hmc_delete_adapter(lpar_uuid: str, adapter_type: str, adapter_uuid: str) -> str:
    """Remove a virtual adapter from an LPAR by its UUID.

    adapter_type is one of: ClientNetworkAdapter, VirtualSCSIClientAdapter,
    VirtualFibreChannelClientAdapter, VirtualNICDedicated. Get adapter UUIDs
    from hmc_list_adapters. Removing an adapter detaches that storage/network
    from the partition.
    """

    async def _go():
        async with client_from_env() as hmc:
            await hmc.delete_child("LogicalPartition", lpar_uuid, adapter_type, adapter_uuid)
            return f"Deleted {adapter_type} {adapter_uuid} from {lpar_uuid}"

    return _run(_go())


# ---------------------------------------------------------------------- #
# Virtual storage (Volume Groups, Virtual Disks, mappings)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_list_volume_groups(vios_uuid: str) -> list[dict[str, Any]]:
    """List Volume Groups on a VIOS.

    Each Volume Group shows free space (MiB), the physical volumes backing it
    and the virtual disks already carved out. Find the VIOS UUID with
    hmc_list_vios.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_volume_groups(vios_uuid)

    return _run(_go())


@mcp.tool
def hmc_create_volume_group(
    vios_uuid: str, name: str, physical_volumes: list[str]
) -> dict[str, Any] | None:
    """Create a Volume Group on a VIOS from one or more physical volumes.

    physical_volumes is a list of free PV device names (e.g. ['hdisk10']). Use
    the GetFreePhysicalVolumes job / VIOS 'lspv' to find unused disks. This
    pools the disks so virtual disks can be carved out for LPARs.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.create_volume_group(vios_uuid, name, physical_volumes)

    return _run(_go())


@mcp.tool
def hmc_create_virtual_disk(
    vios_uuid: str, vg_uuid: str, disk_name: str, capacity_mb: int
) -> dict[str, Any] | None:
    """Create a Virtual Disk (logical volume) inside a Volume Group.

    capacity_mb is the size in MiB. The disk becomes backing storage that you
    then attach to an LPAR with hmc_map_storage_to_lpar (storage_kind
    'VirtualDisk'). Find vg_uuid with hmc_list_volume_groups.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.create_virtual_disk(vios_uuid, vg_uuid, disk_name, capacity_mb)

    return _run(_go())


@mcp.tool
def hmc_map_storage_to_lpar(
    vios_uuid: str,
    storage_name: str,
    lpar_uuid: str,
    storage_kind: str = "VirtualDisk",
    target_device: str | None = None,
) -> dict[str, Any] | None:
    """Map backing storage to an LPAR via a Virtual SCSI mapping on a VIOS.

    storage_kind is 'VirtualDisk' (a logical volume created with
    hmc_create_virtual_disk) or 'PhysicalVolume' (a whole hdisk). storage_name
    is the DiskName / device name. lpar_uuid is the client partition to attach
    to (it must already have a vSCSI adapter paired to this VIOS — see
    hmc_add_vscsi_adapter). target_device optionally pins the vtscsi device
    name on the VIOS.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.map_storage_to_lpar(
                vios_uuid, storage_kind, storage_name, lpar_uuid, target_device
            )

    return _run(_go())


def main_stdio() -> None:
    mcp.run()


def main_http(host: str = "127.0.0.1", port: int = 8000) -> None:
    mcp.run(transport="streamable-http", host=host, port=port)
