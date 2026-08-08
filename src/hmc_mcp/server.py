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
from .config import HMCConfig
from .jobs import power_off_lpar_job, power_on_lpar_job, vios_install_job
from .ssh import run_hmc_command
from .templates import PARTITION_TYPES, build_hmc_user_document, build_lpar_document, build_vios_document

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
# HMC CLI passthrough (SSH)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_run_command(cmd: str) -> str:
    """Execute an arbitrary HMC CLI command over SSH and return its output.

    WARNING: This tool executes arbitrary commands on the HMC with the
    credentials configured in HMC_USER / HMC_PASSWORD (or HMC_SSH_KEY_FILE).
    It is an operator escape-hatch equivalent to Ansible ``hmc_command``.
    Use only for HMC CLI operations that have no dedicated MCP tool.

    Authentication follows the same env-var configuration as all other tools:
    set HMC_SSH_KEY_FILE to use key-based auth, otherwise password auth is used.

    Reference: https://www.ibm.com/docs/en/power10/7063-CR1?topic=hmc-commands
    """
    config = HMCConfig()
    return _run(run_hmc_command(config, cmd))


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
# VIOS lifecycle (create / delete / install)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_create_vios(
    system_uuid: str,
    name: str,
    min_memory: int = 512,
    desired_memory: int = 4096,
    max_memory: int = 8192,
    desired_vcpus: int = 2,
    min_vcpus: int = 1,
    max_vcpus: int = 4,
    desired_procs: float = 0.5,
    min_procs: float = 0.1,
    max_procs: float = 1.0,
) -> dict[str, Any] | None:
    """Create a new Virtual IO Server (VIOS) partition on a managed system.

    system_uuid is the target managed system (find it with hmc_list_systems).
    Memory values are in MiB; procs are shared processing units (fractional
    ok). The VIOS is created powered off with default settings — install the
    OS with hmc_install_vios before using it as a storage/network server.
    This creates a real partition — confirm name/system_uuid before calling.
    """
    xml = build_vios_document(
        name=name,
        min_memory=min_memory,
        desired_memory=desired_memory,
        max_memory=max_memory,
        desired_vcpus=desired_vcpus,
        min_vcpus=min_vcpus,
        max_vcpus=max_vcpus,
        desired_procs=desired_procs,
        min_procs=min_procs,
        max_procs=max_procs,
    )

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.create_logical_partition(system_uuid, xml)

    return _run(_go())


@mcp.tool
def hmc_delete_vios(vios_uuid: str) -> str:
    """Delete (destroy) a VIOS partition by UUID.

    The VIOS must be powered off first (use hmc_power_off_vios and confirm
    with hmc_lpar_state). This permanently removes the VIOS and its profiles
    from the HMC — it is irreversible. Confirm the UUID with hmc_list_vios
    before calling.
    """

    async def _go():
        async with client_from_env() as hmc:
            await hmc.delete_logical_partition(vios_uuid)
            return f"Deleted VIOS {vios_uuid}"

    return _run(_go())


@mcp.tool
def hmc_install_vios(
    vios_uuid: str,
    nim_ip: str,
    nim_gateway: str,
    nim_subnetmask: str,
    vios_ip: str,
    vlan_id: str = "0",
    timeout: int = 60,
) -> dict[str, Any] | None:
    """Submit a NIM-based VIOS installation job.

    vios_uuid is the UUID of an existing (powered-off) VIOS partition. The
    VIOS will PXE-boot from the NIM server at nim_ip to install its OS.
    nim_gateway and nim_subnetmask define the network for the NIM install
    boot; vios_ip is the IP address the VIOS uses during the NIM install;
    vlan_id is the VLAN tag for the install network (use "0" for untagged).
    timeout is the job timeout in minutes (default 60). Returns the submitted
    job — poll hmc_get_job for status.
    """

    async def _go():
        async with client_from_env() as hmc:
            job_xml = vios_install_job(nim_ip, nim_gateway, nim_subnetmask, vios_ip, vlan_id, timeout)
            return await hmc.submit_job(
                f"/rest/api/uom/VirtualIOServer/{vios_uuid}/do/InstallVIOS",
                job_xml,
            )

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


# ---------------------------------------------------------------------- #
# Live Partition Mobility (LPM)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_migrate_lpar(
    lpar_uuid: str,
    target_system: str,
    target_profile_name: str | None = None,
    wait_time: int | None = None,
) -> dict[str, Any] | None:
    """Live-migrate (LPM) an LPAR to another managed system.

    Submits a Migrate job. target_system is the target managed-system name.
    Optionally pin the target profile / wait time. Poll hmc_get_job for status.
    Run hmc_migrate_validate_lpar first to pre-check.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.lpar_migrate(lpar_uuid, target_system, target_profile_name, wait_time=wait_time)

    return _run(_go())


@mcp.tool
def hmc_migrate_validate_lpar(
    lpar_uuid: str,
    target_system: str,
    target_profile_name: str | None = None,
    wait_time: int | None = None,
) -> dict[str, Any] | None:
    """Validate whether an LPM migration of an LPAR to target_system would succeed."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.lpar_migrate_validate(lpar_uuid, target_system, target_profile_name, wait_time=wait_time)

    return _run(_go())


@mcp.tool
def hmc_migrate_abort_lpar(lpar_uuid: str) -> dict[str, Any] | None:
    """Abort an in-progress LPM migration of an LPAR."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.lpar_migrate_abort(lpar_uuid)

    return _run(_go())


@mcp.tool
def hmc_migrate_recover_lpar(lpar_uuid: str) -> dict[str, Any] | None:
    """Recover an LPAR after a failed LPM migration."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.lpar_migrate_recover(lpar_uuid)

    return _run(_go())


@mcp.tool
def hmc_remote_restart_lpar(lpar_uuid: str, target_system: str) -> dict[str, Any] | None:
    """Remote-restart a failed LPAR on another managed system."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.lpar_remote_restart(lpar_uuid, target_system)

    return _run(_go())


# ---------------------------------------------------------------------- #
# Virtual Network management
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_list_virtual_switches(system_uuid: str) -> list[dict[str, Any]]:
    """List VirtualSwitches on a managed system (names, SwitchIDs, mode).

    The SwitchID is what hmc_create_virtual_network and hmc_add_network_adapter
    reference.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_virtual_switches(system_uuid)

    return _run(_go())


@mcp.tool
def hmc_list_virtual_networks(system_uuid: str) -> list[dict[str, Any]]:
    """List Virtual Networks (VLANs) on a managed system."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_virtual_networks(system_uuid)

    return _run(_go())


@mcp.tool
def hmc_create_virtual_network(
    system_uuid: str,
    name: str,
    vlan_id: int,
    vswitch_id: int,
    tagged: bool = False,
) -> dict[str, Any] | None:
    """Create a Virtual Network (VLAN) on a managed system.

    vswitch_id is the numeric SwitchID of the backing VirtualSwitch (see
    hmc_list_virtual_switches). tagged sets whether bridged traffic keeps the
    VLAN tag.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.create_virtual_network(system_uuid, name, vlan_id, vswitch_id, tagged=tagged)

    return _run(_go())


@mcp.tool
def hmc_delete_virtual_network(system_uuid: str, network_uuid: str) -> str:
    """Delete a Virtual Network from a managed system.

    Note: a network referenced by a NetworkBridge, or equal to a trunk
    adapter's PVID, cannot be deleted until the bridge is removed.
    """

    async def _go():
        async with client_from_env() as hmc:
            await hmc.delete_virtual_network(system_uuid, network_uuid)
            return f"Deleted VirtualNetwork {network_uuid} from {system_uuid}"

    return _run(_go())


@mcp.tool
def hmc_list_network_bridges(system_uuid: str) -> list[dict[str, Any]]:
    """List NetworkBridges (Shared Ethernet Adapters) on a managed system."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_network_bridges(system_uuid)

    return _run(_go())


# ---------------------------------------------------------------------- #
# Template Library
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_list_partition_templates() -> list[dict[str, Any]]:
    """List all partition templates in the HMC template library."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_partition_templates()

    return _run(_go())


@mcp.tool
def hmc_get_partition_template(template_uuid: str) -> dict[str, Any] | None:
    """Get one partition template by UUID (full config the template captures)."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_partition_template(template_uuid)

    return _run(_go())


@mcp.tool
def hmc_deploy_partition_template(
    draft_template_uuid: str, target_system_uuid: str
) -> dict[str, Any] | None:
    """Deploy a partition from a *draft* partition template.

    draft_template_uuid is the transformed/replica template UUID (produced by
    capture/transform), target_system_uuid is the managed system to create the
    partition on. Submits a Deploy job; poll hmc_get_job for status.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.deploy_partition_template(draft_template_uuid, target_system_uuid)

    return _run(_go())


# ---------------------------------------------------------------------- #
# Managed-system / VIOS power
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_power_on_system(system_uuid: str) -> dict[str, Any] | None:
    """Power on a managed system (PowerOn job). Poll hmc_get_job for status."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.power_on_system(system_uuid)

    return _run(_go())


@mcp.tool
def hmc_power_off_system(system_uuid: str, immediate: bool = False) -> dict[str, Any] | None:
    """Power off a managed system (PowerOff job). immediate skips graceful shutdown."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.power_off_system(system_uuid, immediate)

    return _run(_go())


@mcp.tool
def hmc_power_on_vios(vios_uuid: str) -> dict[str, Any] | None:
    """Power on a VIOS (PowerOn job). Poll hmc_get_job for status."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.power_on_vios(vios_uuid)

    return _run(_go())


@mcp.tool
def hmc_power_off_vios(vios_uuid: str, immediate: bool = False) -> dict[str, Any] | None:
    """Power off a VIOS (PowerOff job). immediate skips graceful shutdown."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.power_off_vios(vios_uuid, immediate)

    return _run(_go())


# ---------------------------------------------------------------------- #
# Virtual Media Repository / optical media
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_create_media_repository(
    vios_uuid: str, vg_uuid: str, size_mb: int
) -> dict[str, Any] | None:
    """Create the Virtual Media Repository (named VMLibrary) on a Volume Group.

    The repository holds file-backed ISO images for client partitions; only one
    can exist per VIOS. size_mb is RepositorySize.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.create_media_repository(vios_uuid, vg_uuid, size_mb)

    return _run(_go())


@mcp.tool
def hmc_create_optical_media(
    vios_uuid: str, vg_uuid: str, media_name: str, size_mb: int
) -> dict[str, Any] | None:
    """Create a blank VirtualOpticalMedia (ISO container) in the media repository.

    Only blank media can be created via the API; media_name is the file name
    (e.g. 'aix.iso'), size_mb is MediaSize.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.create_optical_media(vios_uuid, vg_uuid, media_name, size_mb)

    return _run(_go())


@mcp.tool
def hmc_delete_media_repository(vios_uuid: str, vg_uuid: str) -> dict[str, Any] | None:
    """Delete the Virtual Media Repository from a Volume Group."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.delete_media_repository(vios_uuid, vg_uuid)

    return _run(_go())


# ---------------------------------------------------------------------- #
# Cluster / Shared Storage Pool (SSP)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_list_clusters() -> list[dict[str, Any]]:
    """List Clusters (sets of VIOS nodes sharing a storage pool)."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_clusters()

    return _run(_go())


@mcp.tool
def hmc_list_shared_storage_pools() -> list[dict[str, Any]]:
    """List Shared Storage Pools (capacity, free space, logical units)."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_shared_storage_pools()

    return _run(_go())


@mcp.tool
def hmc_get_shared_storage_pool(ssp_uuid: str) -> dict[str, Any] | None:
    """Get one Shared Storage Pool by UUID (physical volumes, logical units)."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_shared_storage_pool(ssp_uuid)

    return _run(_go())


@mcp.tool
def hmc_create_logical_unit(
    cluster_uuid: str,
    lu_name: str,
    lu_size_gb: int,
    lu_type: str = "THIN",
    device_type: str = "VirtualIO_Disk",
    cloned_from: str | None = None,
) -> dict[str, Any] | None:
    """Create a Logical Unit (file-backed disk) in a Cluster/SSP.

    Submits a CreateLogicalUnit job and returns it — poll hmc_get_job for
    status; the result holds the new LU's UDID in LUCreated. lu_type is THIN
    or THICK; device_type is VirtualIO_Disk or VirtualIO_Image. cloned_from is
    an optional source LU UDID to clone. Find cluster_uuid with
    hmc_list_clusters.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.create_logical_unit(
                cluster_uuid, lu_name, lu_size_gb, lu_type, device_type, cloned_from
            )

    return _run(_go())


@mcp.tool
def hmc_delete_logical_unit(cluster_uuid: str, lu_udid: str) -> dict[str, Any] | None:
    """Delete a Logical Unit from a Cluster/SSP by its UDID (a job)."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.delete_logical_unit(cluster_uuid, lu_udid)

    return _run(_go())


# ---------------------------------------------------------------------- #
# Performance and Capacity Monitoring (PCM)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_get_pcm_preferences(category: str, uuid: str) -> dict[str, Any]:
    """Get PCM monitoring preferences for a resource.

    category is e.g. 'ManagedSystem' or 'LogicalPartition'. Returns flags like
    LongTermMonitorEnabled, AggregationEnabled, ShortTermMonitorEnabled,
    ComputeLTMEnabled, EnergyMonitorEnabled.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_pcm_preferences(category, uuid)

    return _run(_go())


@mcp.tool
def hmc_set_pcm_preferences(
    category: str,
    uuid: str,
    long_term_monitor: bool | None = None,
    aggregation: bool | None = None,
    short_term_monitor: bool | None = None,
    compute_ltm: bool | None = None,
    energy_monitor: bool | None = None,
) -> str:
    """Enable/disable PCM data collection for a resource.

    Only the flags you set are changed. Turning on aggregation implicitly
    enables long-term monitoring on the HMC. Long-term + aggregation are
    required before processed/aggregated metrics become available.
    """
    flags: dict[str, bool] = {}
    if long_term_monitor is not None:
        flags["LongTermMonitorEnabled"] = long_term_monitor
    if aggregation is not None:
        flags["AggregationEnabled"] = aggregation
    if short_term_monitor is not None:
        flags["ShortTermMonitorEnabled"] = short_term_monitor
    if compute_ltm is not None:
        flags["ComputeLTMEnabled"] = compute_ltm
    if energy_monitor is not None:
        flags["EnergyMonitorEnabled"] = energy_monitor
    if not flags:
        return "No preference flags supplied; nothing to change."

    async def _go():
        async with client_from_env() as hmc:
            await hmc.set_pcm_preferences(category, uuid, **flags)
            return f"Updated PCM preferences on {category} {uuid}: {flags}"

    return _run(_go())


@mcp.tool
def hmc_get_processed_metrics(
    category: str,
    uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
    fetch: bool = False,
) -> Any:
    """Get processed PCM metrics (30s granularity, ~2h retention).

    Timestamps are ISO-8601 UTC (yyyy-MM-ddTHH:mm:ssZ); start_ts is required.
    By default returns the list of JSON links; set fetch=True to also download
    and return the metric JSON of the most recent link.
    """
    return _metrics_tool(category, uuid, "processed", start_ts, end_ts, no_of_samples, fetch)


@mcp.tool
def hmc_get_aggregated_metrics(
    category: str,
    uuid: str,
    start_ts: str,
    end_ts: str | None = None,
    no_of_samples: int | None = None,
    fetch: bool = False,
) -> Any:
    """Get aggregated PCM metrics (long-term rollup for trend analysis).

    Same arguments as hmc_get_processed_metrics. Requires aggregation to be
    enabled in PCM preferences.
    """
    return _metrics_tool(category, uuid, "aggregated", start_ts, end_ts, no_of_samples, fetch)


def _metrics_tool(
    category: str,
    uuid: str,
    kind: str,
    start_ts: str,
    end_ts: str | None,
    no_of_samples: int | None,
    fetch: bool,
) -> Any:
    async def _go():
        async with client_from_env() as hmc:
            fn = hmc.get_processed_metrics if kind == "processed" else hmc.get_aggregated_metrics
            links = await fn(category, uuid, start_ts, end_ts, no_of_samples)
            if not fetch or not links:
                return links
            # Fetch the most recent metrics document.
            return await hmc.fetch_json(links[-1]["link"])

    return _run(_go())


def main_stdio() -> None:
    mcp.run()


def main_http(host: str = "127.0.0.1", port: int = 8000) -> None:
    mcp.run(transport="streamable-http", host=host, port=port)


# ---------------------------------------------------------------------- #
# HMC user management
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_list_users(user_type: str = "all") -> str:
    """List HMC user accounts.

    user_type filters by account type: 'local' (local HMC accounts),
    'kerberos' (Kerberos/LDAP-backed accounts), or 'all' (default).
    Returns the raw XML response from /rest/api/web/HmcUser.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_hmc_users(user_type)

    return _run(_go())


@mcp.tool
def hmc_get_user(name: str) -> str:
    """Get details for one HMC user account by username.

    Returns the raw XML response from /rest/api/web/HmcUser/{name}.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_hmc_user(name)

    return _run(_go())


@mcp.tool
def hmc_create_user(
    name: str,
    taskrole: str,
    password: str,
    description: str = "",
    pwage: int = 0,
) -> str:
    """Create a new HMC local user account.

    name is the login username. taskrole controls what the user can do
    (e.g. 'hmcoperator', 'hmcviewer', 'hmcsuperadmin'). password is the
    initial password. description is optional. pwage is the password
    expiration in days (0 = never expires). This creates a real account —
    confirm the taskrole before calling.
    """
    xml = build_hmc_user_document(
        username=name,
        taskrole=taskrole,
        password=password,
        description=description or None,
        pwage=pwage,
    )

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.create_hmc_user(xml)

    return _run(_go())


@mcp.tool
def hmc_modify_user(
    name: str,
    taskrole: str | None = None,
    password: str | None = None,
    description: str | None = None,
    enable: bool | None = None,
) -> str:
    """Modify an existing HMC user account.

    Only the fields you supply are changed. enable=True re-enables a
    disabled account; enable=False disables it. Use hmc_get_user to
    confirm the current state before calling.
    """
    xml = build_hmc_user_document(
        taskrole=taskrole,
        password=password,
        description=description,
        enable=enable,
    )

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.modify_hmc_user(name, xml)

    return _run(_go())


@mcp.tool
def hmc_delete_user(name: str) -> str:
    """Delete an HMC user account by username.

    This permanently removes the account — it is irreversible. Confirm
    the username with hmc_get_user before calling.
    """

    async def _go():
        async with client_from_env() as hmc:
            await hmc.delete_hmc_user(name)
            return f"Deleted HMC user {name}"

    return _run(_go())
