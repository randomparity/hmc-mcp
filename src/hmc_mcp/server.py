"""FastMCP server exposing the IBM HMC REST API as MCP tools.

Run:
    hmc-mcp serve            # stdio transport (default, for agents)
    hmc-mcp serve --http     # streamable HTTP on 127.0.0.1:8000

Authentication:
    REST tools authenticate via HMC_USER/HMC_PASSWORD (see
    ``client_from_env``). SSH-passthrough tools (those that run HMC CLI
    commands via ``run_hmc_command``) use the same env-var configuration as
    ``hmc_run_command``: set HMC_SSH_KEY_FILE for key-based auth, otherwise
    HMC_PASSWORD is used.

Addressing:
    All tools address managed systems and logical partitions by UUID
    (``system_uuid`` / ``lpar_uuid``). SSH-passthrough tools resolve a UUID
    to its CLI name with a REST lookup (via ``client_from_env``) before
    running the HMC command.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import FastMCP

from .common import client_from_env
from .config import HMCConfig
from .jobs import (
    firmware_update_job,
    hmc_update_job,
    hmc_upgrade_job,
    install_lpar_job,
    power_off_lpar_job,
    power_on_lpar_job,
    vios_install_job,
    vios_update_job,
    vios_upgrade_job,
)
from .ssh import list_io_slots, run_hmc_command
from .templates import (
    build_dlpar_mem_document,
    build_dlpar_proc_document,
    build_hmc_user_document,
    build_ldap_config_document,
    build_lpar_document,
    build_managed_system_document,
    build_password_policy_document,
    build_vios_document,
)

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
# UUID -> CLI-name resolution (REST lookup for SSH passthrough tools)
# ---------------------------------------------------------------------- #


async def _system_name(hmc, system_uuid: str) -> str:
    """Resolve a managed-system UUID to its CLI SystemName via REST."""
    entry = await hmc.get_managed_system(system_uuid)
    if not entry or "SystemName" not in entry.get("Resource", {}):
        raise ValueError(
            f"Could not resolve system UUID {system_uuid!r} to a system name. "
            "Use hmc_list_systems to find the system_uuid."
        )
    return entry["Resource"]["SystemName"]


async def _lpar_name(hmc, lpar_uuid: str) -> str:
    """Resolve an LPAR UUID to its CLI PartitionName via REST."""
    entry = await hmc.get_logical_partition(lpar_uuid)
    if not entry or "PartitionName" not in entry.get("Resource", {}):
        raise ValueError(
            f"Could not resolve LPAR UUID {lpar_uuid!r} to a partition name. "
            "Use hmc_list_lpars to find the lpar_uuid."
        )
    return entry["Resource"]["PartitionName"]


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
def hmc_vios_mappings(vios_uuid: str) -> dict[str, Any] | None:
    """Return VIOS device mapping facts (vSCSI, NPIV, virtual optical).

    Fetches the ViosStorageDetail group for the given VIOS UUID and returns
    the parsed entry, which contains VirtualSCSIMappings (physical volumes and
    virtual disks served to LPARs) and VirtualFibreChannelMappings (NPIV port
    mappings). Equivalent to Ansible ``vios_mapping_facts``.

    Find VIOS UUIDs with hmc_list_vios.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_vios_storage_detail(vios_uuid)

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
    os_type: str | None = None,
    keylock: str | None = None,
    max_virtual_slots: int | None = None,
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
    os_type: target OS — ``aix``, ``linux``, or ``ibmi``.
    keylock: initial keylock position — ``normal``, ``manual``, or ``auto``.
    max_virtual_slots: maximum number of virtual I/O slots.
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
        os_type=os_type,
        keylock=keylock,
        max_virtual_slots=max_virtual_slots,
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
def hmc_dlpar_proc(
    lpar_uuid: str,
    desired_procs: float | None = None,
    min_procs: float | None = None,
    max_procs: float | None = None,
    desired_vcpus: int | None = None,
    min_vcpus: int | None = None,
    max_vcpus: int | None = None,
    dedicated: bool = False,
    uncapped: bool = True,
) -> dict[str, Any] | None:
    """DLPAR processor hot-plug: change CPU resources on a running LPAR.

    Posts a minimal PartitionProcessorConfiguration document to the HMC.
    Only the fields you pass are changed. For shared partitions, procs are
    processing units (may be fractional, e.g. 0.5); vcpus are virtual
    processor counts (ints). Set dedicated=True for whole-CPU assignment.

    If the LPAR does not have an active RMC connection, the change is
    profile-only and takes effect on next activation (no reboot is triggered).
    """
    xml = build_dlpar_proc_document(
        desired_procs=desired_procs,
        min_procs=min_procs,
        max_procs=max_procs,
        desired_vcpus=desired_vcpus,
        min_vcpus=min_vcpus,
        max_vcpus=max_vcpus,
        dedicated=dedicated,
        uncapped=uncapped,
    )

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.modify_logical_partition(lpar_uuid, xml)

    return _run(_go())


@mcp.tool
def hmc_modify_system(
    system_uuid: str,
    new_name: str | None = None,
    power_off_policy: str | None = None,
    power_on_lpar_start_policy: str | None = None,
    pend_mem_region_size: int | None = None,
    requested_num_sys_huge_pages: int | None = None,
    mem_mirroring_mode: str | None = None,
) -> dict[str, Any] | None:
    """Modify a managed system's configuration.

    Only the fields you pass are changed; omitted fields are left as-is.

    system_uuid: UUID of the managed system (from hmc_list_systems).
    new_name: rename the managed system.
    power_off_policy: system power-off policy (e.g. 'autooff').
    power_on_lpar_start_policy: LPAR auto-start policy on system power-on.
    pend_mem_region_size: pending memory region size (MiB).
    requested_num_sys_huge_pages: number of huge memory pages to allocate.
    mem_mirroring_mode: memory mirroring mode (e.g. 'none', 'sys_firmware_only').
    """
    xml = build_managed_system_document(
        new_name=new_name,
        power_off_policy=power_off_policy,
        power_on_lpar_start_policy=power_on_lpar_start_policy,
        pend_mem_region_size=pend_mem_region_size,
        requested_num_sys_huge_pages=requested_num_sys_huge_pages,
        mem_mirroring_mode=mem_mirroring_mode,
    )

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.modify_managed_system(system_uuid, xml)

    return _run(_go())


@mcp.tool
def hmc_dlpar_mem(
    lpar_uuid: str,
    desired_mem: int | None = None,
    min_mem: int | None = None,
    max_mem: int | None = None,
) -> dict[str, Any] | None:
    """DLPAR memory hot-plug: change memory resources on a running LPAR.

    Posts a minimal PartitionMemoryConfiguration document to the HMC.
    Memory values are in MiB. Only the fields you pass are changed.

    If the LPAR does not have an active RMC connection, the change is
    profile-only and takes effect on next activation (no reboot is triggered).
    """
    xml = build_dlpar_mem_document(
        desired_mem=desired_mem,
        min_mem=min_mem,
        max_mem=max_mem,
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
    hmc_find_lpar before calling. Returns a confirmation string (immediate
    delete — no job to poll).
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
    before calling. Returns a confirmation string (immediate delete — no job
    to poll).
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


@mcp.tool
def hmc_install_lpar_os(
    lpar_uuid: str,
    nim_ip: str,
    nim_gateway: str,
    nim_subnetmask: str,
    lpar_ip: str,
    vlan_id: str = "0",
    timeout: int = 60,
) -> dict[str, Any] | None:
    """Submit a NIM-based LPAR OS installation job.

    lpar_uuid is the UUID of an existing (powered-off) LPAR. The LPAR will
    PXE-boot from the NIM server at nim_ip to install its OS.
    nim_gateway and nim_subnetmask define the network for the NIM install
    boot; lpar_ip is the IP address the LPAR uses during the NIM install;
    vlan_id is the VLAN tag for the install network (use "0" for untagged).
    timeout is the job timeout in minutes (default 60). Returns the submitted
    job — poll hmc_get_job for status.
    """

    async def _go():
        async with client_from_env() as hmc:
            job_xml = install_lpar_job(nim_ip, nim_gateway, nim_subnetmask, lpar_ip, vlan_id, timeout)
            return await hmc.submit_job(
                f"/rest/api/uom/LogicalPartition/{lpar_uuid}/do/InstallLPAR",
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
    from the partition. Returns a confirmation string (immediate delete — no
    job to poll).
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
    adapter's PVID, cannot be deleted until the bridge is removed. Returns a
    confirmation string (immediate delete — no job to poll).
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


@mcp.tool
def hmc_list_fc_ports(system_uuid: str, lpar_uuid: str | None = None) -> list[dict]:
    """List Virtual Fibre Channel (NPIV) adapters for a managed system via the HMC CLI.

    Runs ``lshwres -r virtualio --rsubtype fc --level lpar -m <system_name>``
    on the HMC via SSH and returns parsed dicts with fields including
    lpar_name, slot_num, wwpns, and remote_lpar_id.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs. Pass lpar_uuid to restrict results to a single
    partition. Use hmc_list_systems to find system_uuid and hmc_list_lpars
    to find lpar_uuid.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    import csv
    import io

    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid) if lpar_uuid else None
        cmd = f"lshwres -r virtualio --rsubtype fc --level lpar -m {system_name}"
        if lpar_name:
            cmd += f" --filter lpar_names={lpar_name}"
        config = HMCConfig()
        raw = await run_hmc_command(config, cmd)
        if not raw.strip():
            return []
        reader = csv.DictReader(io.StringIO(raw.strip()))
        return [dict(row) for row in reader]

    return _run(_go())


@mcp.tool
def hmc_list_sea_adapters(system_uuid: str, lpar_uuid: str | None = None) -> list[dict]:
    """List Shared Ethernet Adapter (SEA) virtual Ethernet ports via the HMC CLI.

    Runs ``lshwres -r virtualio --rsubtype eth --level lpar -m <system_name>
    -F lpar_name,port_vlan_id,vswitch,state,trunk_priority`` on the HMC via
    SSH and returns parsed dicts with those five fields.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs. Pass lpar_uuid to restrict results to a single
    partition. Use hmc_list_systems to find system_uuid and hmc_list_lpars
    to find lpar_uuid.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    fields = "lpar_name,port_vlan_id,vswitch,state,trunk_priority"

    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid) if lpar_uuid else None
        cmd = (
            f"lshwres -r virtualio --rsubtype eth --level lpar -m {system_name}"
            f" -F {fields}"
        )
        if lpar_name:
            cmd += f" --filter lpar_names={lpar_name}"
        config = HMCConfig()
        raw = await run_hmc_command(config, cmd)
        if not raw.strip():
            return []
        keys = fields.split(",")
        result = []
        for line in raw.strip().splitlines():
            values = line.split(",", len(keys) - 1)
            result.append(dict(zip(keys, values)))
        return result

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
def hmc_delete_media_repository(vios_uuid: str, vg_uuid: str) -> str:
    """Delete the Virtual Media Repository from a Volume Group.

    This is an immediate (synchronous) delete — it returns a confirmation
    string once the HMC has applied the change; there is no job to poll.
    """

    async def _go():
        async with client_from_env() as hmc:
            await hmc.delete_media_repository(vios_uuid, vg_uuid)
            return f"Deleted media repository from VolumeGroup {vg_uuid}"

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
    """Delete a Logical Unit from a Cluster/SSP by its UDID.

    Submits a DeleteLogicalUnit job and returns it — poll hmc_get_job for
    status (an asynchronous delete, unlike the immediate delete tools).
    """

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
    the username with hmc_get_user before calling. Returns a confirmation
    string (immediate delete — no job to poll).
    """

    async def _go():
        async with client_from_env() as hmc:
            await hmc.delete_hmc_user(name)
            return f"Deleted HMC user {name}"

    return _run(_go())


# ---------------------------------------------------------------------- #
# HMC password policy management
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_list_password_policies(policy_type: str = "policies") -> str:
    """List HMC password policies.

    policy_type selects what to return: 'policies' (default) returns the list
    of defined password policies, 'status' returns activation status.
    Returns the raw XML response from /rest/api/web/HmcPasswordPolicy.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_password_policies(policy_type)

    return _run(_go())


@mcp.tool
def hmc_create_password_policy(
    policy_name: str,
    pwage: int = 0,
    min_length: int = 8,
    min_digits: int = 0,
    min_uppercase: int = 0,
    min_lowercase: int = 0,
    min_special: int = 0,
    hist_size: int = 0,
    warn_pwage: int = 0,
    min_pwage: int = 0,
) -> str:
    """Create a new HMC password policy.

    policy_name is the unique name for the policy.  pwage is the maximum
    password age in days (0 = never expires).  min_length is the minimum
    password length.  min_digits, min_uppercase, min_lowercase, and
    min_special set character-class minimums.  hist_size controls how many
    previous passwords cannot be reused.  warn_pwage is the number of days
    before expiry to warn the user.  min_pwage is the minimum days before a
    password may be changed.  Confirm the policy_name before calling.
    """
    xml = build_password_policy_document(
        policy_name=policy_name,
        pwage=pwage,
        min_length=min_length,
        min_digits=min_digits,
        min_uppercase=min_uppercase,
        min_lowercase=min_lowercase,
        min_special=min_special,
        hist_size=hist_size,
        warn_pwage=warn_pwage,
        min_pwage=min_pwage,
    )

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.create_password_policy(xml)

    return _run(_go())


@mcp.tool
def hmc_modify_password_policy(
    policy_name: str,
    pwage: int | None = None,
    min_length: int | None = None,
    min_digits: int | None = None,
    min_uppercase: int | None = None,
    min_lowercase: int | None = None,
    min_special: int | None = None,
    hist_size: int | None = None,
    warn_pwage: int | None = None,
    min_pwage: int | None = None,
) -> str:
    """Modify an existing HMC password policy.

    Only the fields you supply are changed.  Use hmc_list_password_policies
    to confirm the current state before calling.  To activate or deactivate a
    policy, use the HMC console — the REST API activates a policy by name via
    the PolicyType=status query path rather than a direct field change.
    """
    xml = build_password_policy_document(
        pwage=pwage,
        min_length=min_length,
        min_digits=min_digits,
        min_uppercase=min_uppercase,
        min_lowercase=min_lowercase,
        min_special=min_special,
        hist_size=hist_size,
        warn_pwage=warn_pwage,
        min_pwage=min_pwage,
    )

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.modify_password_policy(policy_name, xml)

    return _run(_go())


@mcp.tool
def hmc_delete_password_policy(policy_name: str) -> str:
    """Delete an HMC password policy by name.

    This permanently removes the policy — it is irreversible.  Confirm
    the policy_name with hmc_list_password_policies before calling. Returns
    a confirmation string (immediate delete — no job to poll).
    """

    async def _go():
        async with client_from_env() as hmc:
            await hmc.delete_password_policy(policy_name)
            return f"Deleted HMC password policy {policy_name}"

    return _run(_go())


# ---------------------------------------------------------------------- #
# HMC LDAP server configuration
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_list_ldap_config() -> str:
    """Get the current HMC LDAP server configuration.

    Returns the raw XML response from /rest/api/web/HmcLdapServer describing
    the configured LDAP server URL, base DN, bind DN, search filter, and
    HMC group mappings.  Returns an empty string if no LDAP is configured.
    Equivalent to Ansible ``hmc_user`` state=ldap_facts.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_ldap_config()

    return _run(_go())


@mcp.tool
def hmc_configure_ldap(
    server_url: str,
    base_dn: str | None = None,
    bind_dn: str | None = None,
    bind_pw: str | None = None,
    search_filter: str | None = None,
    hmc_groups: str | None = None,
    group_member_attributes: str | None = None,
) -> str:
    """Configure the HMC LDAP server integration.

    server_url is the LDAP or LDAPS URL (e.g. 'ldap://ldap.example.com' or
    'ldaps://ldap.example.com:636'). Only the fields you supply are changed.

    base_dn: LDAP search base (e.g. 'dc=example,dc=com').
    bind_dn: DN of the account used to bind for searches.
    bind_pw: password for the bind account.
    search_filter: LDAP search filter (e.g. '(objectClass=person)').
    hmc_groups: comma-separated LDAP groups mapped to HMC access.
    group_member_attributes: LDAP attribute used for group membership.

    Equivalent to Ansible ``hmc_user`` action=configure_ldap.
    """
    xml = build_ldap_config_document(
        server_url=server_url,
        base_dn=base_dn,
        bind_dn=bind_dn,
        bind_pw=bind_pw,
        search_filter=search_filter,
        hmc_groups=hmc_groups,
        group_member_attributes=group_member_attributes,
    )

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.configure_ldap(xml)

    return _run(_go())


@mcp.tool
def hmc_remove_ldap_config(resource: str) -> str:
    """Remove a component of the HMC LDAP server configuration.

    resource selects what to remove.  Valid values:
      'backup'                  — remove the backup LDAP server
      'ldap'                    — remove the entire LDAP configuration
      'binddn'                  — remove the bind DN
      'bindpw'                  — remove the bind password
      'searchfilter'            — remove the custom search filter
      'hmcgroups'               — remove HMC group mappings
      'groupmemberattributes'   — remove group-member attribute settings

    Equivalent to Ansible ``hmc_user`` action=remove_ldap_config.
    Use hmc_list_ldap_config to inspect the current state before calling.
    Returns the HMC response string (immediate delete — no job to poll).
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.remove_ldap_config(resource)

    return _run(_go())


# ---------------------------------------------------------------------- #
# Update / Upgrade (HMC, VIOS, firmware)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_update_hmc(system_uuid: str, repository: dict) -> dict[str, Any] | None:
    """Submit an HMC software update job (install PTFs).

    repository is a dict describing the software source, e.g.:
        {"type": "nfs", "host": "repo.example.com", "path": "/images/hmc"}
        {"type": "sftp", "host": "repo.example.com", "path": "/hmc", "user": "admin", "sftp_pw": "..."}
        {"type": "disk"}  # use files already on the HMC disk

    Submits an Update job to ManagementConsole; poll hmc_get_job for status.
    system_uuid is the ManagementConsole UUID (from hmc_console_info).
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.submit_job(
                f"/rest/api/uom/ManagementConsole/{system_uuid}/do/Update",
                hmc_update_job(repository),
            )

    return _run(_go())


@mcp.tool
def hmc_upgrade_hmc(system_uuid: str, repository: dict) -> dict[str, Any] | None:
    """Submit an HMC software upgrade job (full version upgrade).

    repository describes the upgrade image source (same format as
    hmc_update_hmc). Submits an Upgrade job to ManagementConsole; poll
    hmc_get_job for status. system_uuid is the ManagementConsole UUID.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.submit_job(
                f"/rest/api/uom/ManagementConsole/{system_uuid}/do/Upgrade",
                hmc_upgrade_job(repository),
            )

    return _run(_go())


@mcp.tool
def hmc_list_available_hmc_ptfs(system_uuid: str) -> dict[str, Any] | None:
    """List available PTFs (fixes) for the HMC software.

    Issues a GET to the ManagementConsole resource with the SoftwareUpdate
    group, which returns available PTF information. system_uuid is the
    ManagementConsole UUID (from hmc_console_info). Does not submit a job.
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.get_uom("ManagementConsole", system_uuid, group="SoftwareUpdate")

    return _run(_go())


@mcp.tool
def hmc_update_vios(vios_uuid: str, repository: dict) -> dict[str, Any] | None:
    """Submit a VIOS software update job.

    repository describes the update image source (same format as
    hmc_update_hmc). Submits an Update job to VirtualIOServer; poll
    hmc_get_job for status. vios_uuid is the VIOS UUID (from hmc_list_vios).
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.submit_job(
                f"/rest/api/uom/VirtualIOServer/{vios_uuid}/do/Update",
                vios_update_job(repository),
            )

    return _run(_go())


@mcp.tool
def hmc_upgrade_vios(vios_uuid: str, repository: dict) -> dict[str, Any] | None:
    """Submit a VIOS software upgrade job.

    repository describes the upgrade image source (same format as
    hmc_update_hmc). Submits an Upgrade job to VirtualIOServer; poll
    hmc_get_job for status. vios_uuid is the VIOS UUID (from hmc_list_vios).
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.submit_job(
                f"/rest/api/uom/VirtualIOServer/{vios_uuid}/do/Upgrade",
                vios_upgrade_job(repository),
            )

    return _run(_go())


@mcp.tool
def hmc_update_firmware(system_uuid: str, repository: dict) -> dict[str, Any] | None:
    """Submit a managed system firmware update job.

    repository describes the firmware image source (same format as
    hmc_update_hmc). Submits an UpdateFirmware job to ManagedSystem; poll
    hmc_get_job for status. system_uuid is the managed system UUID
    (from hmc_list_systems).
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.submit_job(
                f"/rest/api/uom/ManagedSystem/{system_uuid}/do/UpdateFirmware",
                firmware_update_job(repository),
            )

    return _run(_go())


# ---------------------------------------------------------------------- #
# VIOS backup / restore
# ---------------------------------------------------------------------- #

_VALID_BACKUP_TYPES = {"vios", "viosioconfig", "ssp"}


@mcp.tool
def hmc_list_vios_backups(vios_uuid: str) -> str:
    """List existing VIOS backups for a given VIOS UUID.

    Runs ``lsviosbackup -id <vios_uuid>`` on the HMC via SSH and returns
    the raw command output. Find vios_uuid with hmc_list_vios.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    config = HMCConfig()
    return _run(run_hmc_command(config, f"lsviosbackup -id {vios_uuid}"))


@mcp.tool
def hmc_backup_vios(vios_uuid: str, backup_type: str = "vios") -> str:
    """Create a VIOS backup via the HMC CLI.

    Runs ``chviosbackup -id <vios_uuid> -operation backup -type <backup_type>``
    on the HMC via SSH. vios_uuid is the VIOS UUID (from hmc_list_vios).

    backup_type must be one of:
      - ``vios``       — full VIOS configuration backup (default)
      - ``viosioconfig`` — I/O configuration backup
      - ``ssp``        — Shared Storage Pool (cluster) backup

    Returns the raw HMC CLI output. Poll hmc_list_vios_backups to confirm
    the backup was created.
    """
    if backup_type not in _VALID_BACKUP_TYPES:
        raise ValueError(
            f"Invalid backup_type {backup_type!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_BACKUP_TYPES))}"
        )
    config = HMCConfig()
    cmd = f"chviosbackup -id {vios_uuid} -operation backup -type {backup_type}"
    return _run(run_hmc_command(config, cmd))


@mcp.tool
def hmc_restore_vios(vios_uuid: str, backup_name: str) -> str:
    """Restore a VIOS from a named backup via the HMC CLI.

    Runs ``chviosbackup -id <vios_uuid> -operation restore -file <backup_name>``
    on the HMC via SSH. vios_uuid is the VIOS UUID (from hmc_list_vios);
    backup_name is the backup file name as listed by hmc_list_vios_backups.

    WARNING: Restoring overwrites the current VIOS configuration. Confirm
    the vios_uuid and backup_name before calling.

    Returns the raw HMC CLI output.
    """
    config = HMCConfig()
    cmd = f"chviosbackup -id {vios_uuid} -operation restore -file {backup_name}"
    return _run(run_hmc_command(config, cmd))


# ---------------------------------------------------------------------- #
# LPAR Profile Management (backup / restore / sync / I/O slot assignment)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_backup_lpar_profiles(system_uuid: str, file_path: str) -> str:
    """Backup all LPAR profiles on a Power system via the HMC CLI.

    Runs ``bkprofdata -m <system_name> -f <file_path>`` on the HMC via SSH
    and returns the raw command output.

    The system UUID is resolved to its CLI name via REST before the command
    runs.

    **IMPORTANT:** file_path is on the HMC filesystem, not the local machine.
    The backup file will be created at that path on the HMC host.

    Args:
        system_uuid: The UUID of the managed system (Power server).
        file_path: Path on the HMC filesystem where the backup file will be saved.

    Returns:
        The raw HMC CLI output.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
        config = HMCConfig()
        cmd = f"bkprofdata -m {system_name} -f {file_path}"
        return await run_hmc_command(config, cmd)

    return _run(_go())


@mcp.tool
def hmc_restore_lpar_profiles(system_uuid: str, file_path: str) -> str:
    """Restore LPAR profiles from a backup file via the HMC CLI.

    Runs ``rstprofdata -m <system_name> -f <file_path>`` on the HMC via SSH
    and returns the raw command output.

    The system UUID is resolved to its CLI name via REST before the command
    runs.

    **IMPORTANT:** file_path is on the HMC filesystem, not the local machine.
    The backup file must already exist at that path on the HMC host.

    WARNING: Restoring overwrites the current LPAR profile configuration.
    Confirm the system_uuid and file_path before calling.

    Args:
        system_uuid: The UUID of the managed system (Power server).
        file_path: Path on the HMC filesystem where the backup file is located.

    Returns:
        The raw HMC CLI output.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
        config = HMCConfig()
        cmd = f"rstprofdata -m {system_name} -f {file_path}"
        return await run_hmc_command(config, cmd)

    return _run(_go())


@mcp.tool
def hmc_sync_lpar_profile(system_uuid: str, lpar_uuid: str) -> str:
    """Sync an LPAR's running configuration back to its current profile.

    Runs ``chsyscfg -r lpar -m <system_name> -i "name=<lpar_name>,sync_curr_profile=1"``
    on the HMC via SSH and returns the raw command output.

    This operation saves the LPAR's current running configuration to its
    current named profile, overwriting the previous profile definition.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    Args:
        system_uuid: The UUID of the managed system (Power server).
        lpar_uuid: The UUID of the logical partition to sync.

    Returns:
        The raw HMC CLI output.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        cmd = f'chsyscfg -r lpar -m {system_name} -i "name={lpar_name},sync_curr_profile=1"'
        return await run_hmc_command(config, cmd)

    return _run(_go())


@mcp.tool
def hmc_assign_profile_io_slot(
    system_uuid: str, lpar_uuid: str, profile_name: str, drc_index: str
) -> str:
    """Add a physical I/O slot DRC index to an LPAR's profile.

    Runs ``chsyscfg -r prof -m <system_name> -i "name=<profile_name>,io_slots+=<drc_index>//0,lpar_name=<lpar_name>" --force``
    on the HMC via SSH and returns the raw command output.

    This operation appends the specified physical I/O slot to the profile's
    I/O slot list. Use --force to override any conflicts.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    Args:
        system_uuid: The UUID of the managed system (Power server).
        lpar_uuid: The UUID of the logical partition to assign the slot to.
        profile_name: The name of the profile to modify.
        drc_index: The DRC (Dynamic Reconfiguration Connector) index of the physical I/O slot.

    Returns:
        The raw HMC CLI output.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        cmd = f'chsyscfg -r prof -m {system_name} -i "name={profile_name},io_slots+={drc_index}//0,lpar_name={lpar_name}" --force'
        return await run_hmc_command(config, cmd)

    return _run(_go())

# ---------------------------------------------------------------------- #
# LPAR description (SSH CLI path — no REST equivalent)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_get_lpar_description(system_uuid: str, lpar_uuid: str) -> str:
    """Get the description field of an LPAR via the HMC CLI.

    Runs ``lssyscfg -r lpar -m <system_name> --filter lpar_names=<lpar_name>
    -F description`` on the HMC via SSH and returns the raw output (the
    description string, or an empty line if none is set).

    This field is not available via the HMC REST API; it is the same
    description visible in the HMC GUI Partitions tab.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        cmd = f"lssyscfg -r lpar -m {system_name} --filter lpar_names={lpar_name} -F description"
        return await run_hmc_command(config, cmd)

    return _run(_go())


@mcp.tool
def hmc_set_lpar_description(system_uuid: str, lpar_uuid: str, description: str) -> str:
    """Set the description field of an LPAR via the HMC CLI.

    Runs ``chsyscfg -r lpar -m <system_name>
    -i "name=<lpar_name>,description=<description>"`` on the HMC via SSH.

    This field is not settable via the HMC REST API. The description appears
    in the HMC GUI Partitions tab and is useful for recording partition
    ownership, purpose, or current task.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    WARNING: This modifies the LPAR configuration on the HMC. Confirm
    lpar_uuid and system_uuid before calling.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        cmd = f'chsyscfg -r lpar -m {system_name} -i "name={lpar_name},description={description}"'
        return await run_hmc_command(config, cmd)

    return _run(_go())


@mcp.tool
def hmc_get_lpar_msp(system_uuid: str, lpar_uuid: str) -> bool:
    """Get the MSP (Migratable Service Partition) flag of an LPAR via the HMC CLI.

    Runs ``lssyscfg -r lpar -m <system_name> --filter lpar_names=<lpar_name>
    -F msp`` on the HMC via SSH and returns ``True`` if the flag is ``1``,
    ``False`` if ``0``.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        cmd = f"lssyscfg -r lpar -m {system_name} --filter lpar_names={lpar_name} -F msp"
        raw = await run_hmc_command(config, cmd)
        return raw.strip() == "1"

    return _run(_go())


@mcp.tool
def hmc_set_lpar_msp(system_uuid: str, lpar_uuid: str, enabled: bool) -> str:
    """Set the MSP (Migratable Service Partition) flag of an LPAR via the HMC CLI.

    Runs ``chsyscfg -r lpar -m <system_name>
    -i "name=<lpar_name>,msp=<0|1>"`` on the HMC via SSH.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    WARNING: This modifies the LPAR configuration on the HMC. Confirm
    lpar_uuid and system_uuid before calling.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        value = "1" if enabled else "0"
        cmd = f'chsyscfg -r lpar -m {system_name} -i "name={lpar_name},msp={value}"'
        return await run_hmc_command(config, cmd)

    return _run(_go())


# ---------------------------------------------------------------------- #
# SR-IOV adapter mode (SSH CLI path)
# ---------------------------------------------------------------------- #

_VALID_SRIOV_MODES = {"sriov", "dedicated"}


@mcp.tool
def hmc_set_sriov_adapter_mode(
    system_uuid: str,
    adapter_id: str,
    mode: str,
) -> str:
    """Toggle a physical SR-IOV adapter between SR-IOV and dedicated mode.

    Runs ``chhwres -r sriov -m <system_name> -o s --id <adapter_id>
    -a "sriov_adapter_mode=<mode>"`` on the HMC via SSH and returns the raw
    command output.

    The system UUID is resolved to its CLI name via REST before the command
    runs.

    ``adapter_id`` is the physical adapter identifier as reported by
    ``hmc_list_io_slots``.

    ``mode`` must be one of:
      - ``"sriov"``      — enable SR-IOV mode (shared virtual functions)
      - ``"dedicated"``  — disable SR-IOV, use as a dedicated (passthrough) adapter

    WARNING: Changing SR-IOV adapter mode affects all partitions using virtual
    functions on that adapter. Confirm system_uuid and adapter_id before calling.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    if mode not in _VALID_SRIOV_MODES:
        raise ValueError(
            f"Invalid mode {mode!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_SRIOV_MODES))}"
        )

    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
        config = HMCConfig()
        cmd = (
            f'chhwres -r sriov -m {system_name} -o s --id {adapter_id}'
            f' -a "sriov_adapter_mode={mode}"'
        )
        return await run_hmc_command(config, cmd)

    return _run(_go())


# ---------------------------------------------------------------------- #
# LPAR processor compatibility modes (SSH CLI path — no REST equivalent)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_get_proc_compat_modes(system_uuid: str) -> list[str]:
    """Get processor compatibility modes supported by a managed system.

    Runs ``lssyscfg -r sys -m <system_name> -F lpar_proc_compat_modes``
    on the HMC via SSH and returns a list of supported mode strings.

    The system UUID is resolved to its CLI name via REST before the command
    runs.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
        config = HMCConfig()
        cmd = f"lssyscfg -r sys -m {system_name} -F lpar_proc_compat_modes"
        raw = await run_hmc_command(config, cmd)
        if not raw.strip():
            return []
        return [mode.strip() for mode in raw.strip().split(",") if mode.strip()]

    return _run(_go())


@mcp.tool
def hmc_get_lpar_proc_compat(system_uuid: str, lpar_uuid: str) -> dict[str, str]:
    """Get the current and pending processor compatibility modes for an LPAR.

    Runs ``lssyscfg -r lpar -m <system_name> --filter lpar_names=<lpar_name>
    -F pend_lpar_proc_compat_mode,curr_lpar_proc_compat_mode`` on the HMC via SSH.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    Returns a dict with keys "pend" and "curr".

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        cmd = f"lssyscfg -r lpar -m {system_name} --filter lpar_names={lpar_name} -F pend_lpar_proc_compat_mode,curr_lpar_proc_compat_mode"
        raw = await run_hmc_command(config, cmd)
        if not raw.strip():
            return {"pend": "", "curr": ""}
        parts = raw.strip().split(",")
        pend = parts[0].strip() if len(parts) > 0 else ""
        curr = parts[1].strip() if len(parts) > 1 else ""
        return {"pend": pend, "curr": curr}

    return _run(_go())


@mcp.tool
def hmc_set_lpar_proc_compat(system_uuid: str, lpar_uuid: str, mode: str) -> str:
    """Set the processor compatibility mode of an LPAR.

    Runs ``chsyscfg -r lpar -m <system_name> -i "name=<lpar_name>,lpar_proc_compat_mode=<mode>"``
    on the HMC via SSH.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    WARNING: This modifies the LPAR configuration on the HMC. Confirm
    lpar_uuid, system_uuid, and mode before calling.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        cmd = f'chsyscfg -r lpar -m {system_name} -i "name={lpar_name},lpar_proc_compat_mode={mode}"'
        return await run_hmc_command(config, cmd)

    return _run(_go())


# ---------------------------------------------------------------------- #
# Physical I/O slot listing (SSH CLI path)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_list_io_slots(
    system_uuid: str,
    adapter_type: str = "all",
) -> list[dict[str, Any]]:
    """List physical I/O slots on a managed system via the HMC CLI.

    Runs ``lshwres -r io --rsubtype slot -m <system_name>`` on the HMC via
    SSH and returns one dict per slot.  Each dict includes fields such as
    ``drc_name``, ``pci_class``, ``feature_codes``, and ``lpar_name``
    (empty string when the slot is unassigned).

    The system UUID is resolved to its CLI name via REST before the command
    runs.

    adapter_type filters by PCI class:
      - ``"all"``   — return every slot (default)
      - ``"eth"``   — Ethernet adapters (PCI class 0200)
      - ``"sas"``   — SAS/SCSI adapters (PCI class 0104)
      - ``"san"``   — Fibre Channel / SAN adapters (PCI class 0C04)
      - ``"nvme"``  — NVMe adapters (PCI class 0108)

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
        config = HMCConfig()
        return await list_io_slots(config, system_name, adapter_type)

    return _run(_go())


# ---------------------------------------------------------------------- #
# Shared memory pool management (SSH CLI path)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_list_memory_pools(system_uuid: str) -> list[dict[str, Any]]:
    """List shared memory pools on a managed system via the HMC CLI.

    Runs ``lshwres -r mempool -m <system_name>`` on the HMC via SSH and
    returns one dict per pool with fields such as ``pool_name``, ``size``,
    ``lpar_names``, and ``curr_lpar_names`` (comma-separated).

    The system UUID is resolved to its CLI name via REST before the command
    runs.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    from .ssh import _parse_lshwres_output

    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
        config = HMCConfig()
        output = await run_hmc_command(config, f"lshwres -r mempool -m {system_name}")
        return _parse_lshwres_output(output)

    return _run(_go())


@mcp.tool
def hmc_remove_memory_pool(system_uuid: str, pool_name: str) -> str:
    """Remove a shared memory pool from a managed system via the HMC CLI.

    Before issuing the remove command, fetches the current pool list and
    checks whether any LPARs are still assigned to *pool_name*.  If any are
    found the command is **not** executed and a structured error message
    naming the blocking LPARs is returned instead.

    Runs ``chhwres -r mempool -m <system_name> -o r -a <pool_name>`` on
    the HMC via SSH when no LPARs are assigned.

    The system UUID is resolved to its CLI name via REST before the command
    runs.

    WARNING: This permanently removes the pool — confirm system_uuid and
    pool_name before calling. Returns the HMC CLI output (immediate delete —
    no job to poll).

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    from .ssh import _parse_lshwres_output

    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
        config = HMCConfig()

        # Safety check: list pools and look for LPAR assignments.
        list_output = await run_hmc_command(
            config, f"lshwres -r mempool -m {system_name}"
        )
        pools = _parse_lshwres_output(list_output)

        for pool in pools:
            if pool.get("pool_name") == pool_name:
                # curr_lpar_names may be a comma-separated string or empty.
                assigned = pool.get("curr_lpar_names", "").strip()
                if assigned:
                    lpar_list = [
                        lp.strip() for lp in assigned.split(",") if lp.strip()
                    ]
                    return (
                        f"ERROR: Cannot remove memory pool '{pool_name}' on "
                        f"'{system_name}' — the following LPARs are still "
                        f"assigned to it: {', '.join(lpar_list)}. Reassign or "
                        "remove them from the pool before retrying."
                    )
                break

        cmd = f"chhwres -r mempool -m {system_name} -o r -a {pool_name}"
        return await run_hmc_command(config, cmd)

    return _run(_go())


# ---------------------------------------------------------------------- #
# vNIC management (SSH CLI path)
# ---------------------------------------------------------------------- #


@mcp.tool
def hmc_list_vnics(system_uuid: str, lpar_uuid: str) -> list[dict[str, Any]]:
    """List vNICs (SR-IOV-backed Virtual NICs) on an LPAR via the HMC CLI.

    Runs ``lshwres -r virtualio --rsubtype vnic --level lpar -m <system_name>
    --filter lpar_names=<lpar_name>`` on the HMC via SSH and returns one dict
    per vNIC with fields such as ``vnic_id``, ``capacity``, ``vswitch_name``,
    ``port_vlan_id``, and ``backing_devices``.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs. Use ``hmc_list_systems`` to find system_uuid and
    ``hmc_list_lpars`` to find lpar_uuid.

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    from .ssh import _parse_lshwres_output

    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        cmd = (
            f"lshwres -r virtualio --rsubtype vnic --level lpar -m {system_name}"
            f" --filter lpar_names={lpar_name}"
        )
        raw = await run_hmc_command(config, cmd)
        if not raw.strip():
            return []
        return _parse_lshwres_output(raw)

    return _run(_go())


@mcp.tool
def hmc_add_vnic(
    system_uuid: str,
    lpar_uuid: str,
    capacity: int,
    vswitch_name: str,
    port_vlan_id: int,
    backing_devices: str | None = None,
) -> str:
    """Add a vNIC (SR-IOV-backed Virtual NIC) to an LPAR via the HMC CLI.

    Runs ``chhwres -r virtualio --rsubtype vnic -o a -m <system_name>
    --filter lpar_names=<lpar_name> -a "<attrs>"`` on the HMC via SSH.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    **V1 scope boundary:** Only the following parameters are supported in
    this version: ``capacity``, ``vswitch_name``, ``port_vlan_id``, and
    ``backing_devices`` (optional, opaque string passed verbatim).  Complex
    backing-device topology (multi-adapter failover, per-device SR-IOV
    physical port IDs, capacity weights) is a follow-up and explicitly out
    of scope for v1.

    Returns the raw HMC command output on success, or a structured error
    message (starting with ``"ERROR:"``) when the command fails because the
    underlying SR-IOV adapter is not in SR-IOV mode.

    WARNING: This modifies the LPAR configuration on the HMC. Confirm
    system_uuid, lpar_uuid, and vswitch_name before calling.  The
    underlying physical adapter must be in SR-IOV mode (see
    ``hmc_set_sriov_adapter_mode``).

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    import asyncssh

    attrs = f"capacity={capacity},vswitch_name={vswitch_name},port_vlan_id={port_vlan_id}"
    if backing_devices is not None:
        attrs += f",backing_devices={backing_devices}"

    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        cmd = (
            f'chhwres -r virtualio --rsubtype vnic -o a -m {system_name}'
            f' --filter lpar_names={lpar_name}'
            f' -a "{attrs}"'
        )
        try:
            return await run_hmc_command(config, cmd)
        except asyncssh.ProcessError as exc:
            stderr = (exc.stderr or "").strip()
            return (
                f"ERROR: Failed to add vNIC to '{lpar_name}' on '{system_name}'. "
                f"Ensure the underlying SR-IOV adapter is in sriov mode "
                f"(see hmc_set_sriov_adapter_mode). HMC error: {stderr}"
            )

    return _run(_go())


@mcp.tool
def hmc_remove_vnic(system_uuid: str, lpar_uuid: str, vnic_id: str) -> str:
    """Remove a vNIC from an LPAR via the HMC CLI.

    Runs ``chhwres -r virtualio --rsubtype vnic -o r -m <system_name>
    --filter lpar_names=<lpar_name> -a "vnic_id=<vnic_id>"`` on the HMC
    via SSH.

    The system and partition UUIDs are resolved to their CLI names via REST
    before the command runs.

    ``vnic_id`` is the numeric ID as reported by ``hmc_list_vnics``.

    WARNING: This modifies the LPAR configuration on the HMC. Confirm
    system_uuid, lpar_uuid, and vnic_id before calling. Returns the HMC CLI
    output (immediate delete — no job to poll).

    Auth: same env-var configuration as hmc_run_command (see module docstring).
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid)
            lpar_name = await _lpar_name(hmc, lpar_uuid)
        config = HMCConfig()
        cmd = (
            f'chhwres -r virtualio --rsubtype vnic -o r -m {system_name}'
            f' --filter lpar_names={lpar_name}'
            f' -a "vnic_id={vnic_id}"'
        )
        return await run_hmc_command(config, cmd)

    return _run(_go())
