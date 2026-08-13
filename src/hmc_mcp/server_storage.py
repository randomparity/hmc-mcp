"""MCP tools for virtual adapters, virtual storage, media repository, and clusters/SSP.
"""

from __future__ import annotations

from typing import Any, Literal

from ._app import (
    _DESTRUCTIVE,
    _READ_ONLY,
    _resolve_lpar_uuid,
    _resolve_vios_uuid,
    _run,
    mcp,
)

from .common import client_from_env


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_adapters(lpar_name_or_uuid: str, adapter_type: str = "ClientNetworkAdapter", profile: str | None = None) -> list[dict[str, Any]]:
    """List an LPAR's virtual adapters of a given type.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_lpars).
    adapter_type is one of: ClientNetworkAdapter, VirtualSCSIClientAdapter,
    VirtualFibreChannelClientAdapter, VirtualNICDedicated.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            return await hmc.list_child("LogicalPartition", lpar_uuid, adapter_type)

    return _run(_go)


@mcp.tool
def hmc_add_network_adapter(
    lpar_name_or_uuid: str,
    port_vlan_id: int,
    slot_number: int | None = None,
    virtual_switch_id: int | None = None,
    tagged: bool = False,
    mac_address: str | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Add a Virtual Ethernet (client network) adapter to an LPAR.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_lpars).
    port_vlan_id is the PVID / VLAN the adapter connects to (this is how the
    adapter is attached to a VirtualNetwork — match the network's VLAN ID and
    vSwitch). slot_number is the virtual slot (auto-assigned if omitted).
    tagged=True makes it a VLAN-tagged (trunking) adapter; mac_address pins
    the MAC (otherwise the HMC generates one). Modifying a running LPAR is a
    DLPAR operation and needs active RMC.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            return await hmc.add_network_adapter(
                lpar_uuid, port_vlan_id, slot_number, virtual_switch_id, tagged, mac_address
            )

    return _run(_go)


@mcp.tool
def hmc_add_vscsi_adapter(
    lpar_name_or_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    slot_number: int | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Add a Virtual SCSI client adapter to an LPAR, paired to a VIOS.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_lpars).
    vios_partition_id is the integer PartitionID of the serving VIOS (find it
    with hmc_vios), and vios_slot is that VIOS's server-side virtual
    SCSI slot number that owns the backing storage. slot_number is the client
    adapter's virtual slot (auto-assigned if omitted). Storage backing devices
    (disks / logical volumes) are then mapped to this adapter on the VIOS.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            return await hmc.add_vscsi_adapter(lpar_uuid, vios_partition_id, vios_slot, slot_number)

    return _run(_go)


@mcp.tool
def hmc_add_vfc_adapter(
    lpar_name_or_uuid: str,
    vios_partition_id: int,
    vios_slot: int,
    slot_number: int | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Add a Virtual Fibre Channel (NPIV) client adapter to an LPAR.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_lpars).
    vios_partition_id is the integer PartitionID of the serving VIOS and
    vios_slot is its server-side virtual FC slot number. The HMC generates the
    WWPNs. Use this for SAN storage via NPIV instead of vSCSI.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            return await hmc.add_vfc_adapter(lpar_uuid, vios_partition_id, vios_slot, slot_number)

    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_delete_adapter(lpar_name_or_uuid: str, adapter_type: str, adapter_uuid: str, profile: str | None = None) -> str:
    """Remove a virtual adapter from an LPAR by its UUID.

    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_lpars).
    adapter_type is one of: ClientNetworkAdapter, VirtualSCSIClientAdapter,
    VirtualFibreChannelClientAdapter, VirtualNICDedicated. Get adapter UUIDs
    from hmc_list_adapters. Removing an adapter detaches that storage/network
    from the partition. Returns a confirmation string (immediate delete — no
    job to poll).
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            await hmc.delete_child(
                "LogicalPartition", lpar_uuid, adapter_type, adapter_uuid
            )
        return f"Deleted {adapter_type} {adapter_uuid} from {lpar_name_or_uuid}"

    return _run(_go)




@mcp.tool(annotations=_READ_ONLY)
def hmc_list_volume_groups(vios_name_or_uuid: str, profile: str | None = None) -> list[dict[str, Any]]:
    """List Volume Groups on a VIOS.

    vios_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_vios).
    Each Volume Group shows free space (MiB), the physical volumes backing it
    and the virtual disks already carved out.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            vios_uuid = await _resolve_vios_uuid(hmc, vios_name_or_uuid)
            return await hmc.list_volume_groups(vios_uuid)

    return _run(_go)


@mcp.tool
def hmc_create_volume_group(
    vios_name_or_uuid: str, name: str, physical_volumes: list[str], profile: str | None = None
) -> dict[str, Any] | None:
    """Create a Volume Group on a VIOS from one or more physical volumes.

    vios_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_vios).
    physical_volumes is a list of free PV device names (e.g. ['hdisk10']). Use
    the GetFreePhysicalVolumes job / VIOS 'lspv' to find unused disks. This
    pools the disks so virtual disks can be carved out for LPARs.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            vios_uuid = await _resolve_vios_uuid(hmc, vios_name_or_uuid)
            return await hmc.create_volume_group(vios_uuid, name, physical_volumes)

    return _run(_go)


@mcp.tool
def hmc_create_virtual_disk(
    vios_name_or_uuid: str, vg_uuid: str, disk_name: str, capacity_mb: int, profile: str | None = None
) -> dict[str, Any] | None:
    """Create a Virtual Disk (logical volume) inside a Volume Group.

    vios_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_vios).
    capacity_mb is the size in MiB. The disk becomes backing storage that you
    then attach to an LPAR with hmc_map_storage_to_lpar (storage_kind
    'VirtualDisk'). Find vg_uuid with hmc_list_volume_groups.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            vios_uuid = await _resolve_vios_uuid(hmc, vios_name_or_uuid)
            return await hmc.create_virtual_disk(vios_uuid, vg_uuid, disk_name, capacity_mb)

    return _run(_go)


@mcp.tool
def hmc_map_storage_to_lpar(
    vios_name_or_uuid: str,
    storage_name: str,
    lpar_name_or_uuid: str,
    storage_kind: Literal["VirtualDisk", "PhysicalVolume"] = "VirtualDisk",
    target_device: str | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Map backing storage to an LPAR via a Virtual SCSI mapping on a VIOS.

    vios_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_vios).
    lpar_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_lpars). The LPAR must already have a vSCSI adapter
    paired to this VIOS — see hmc_add_vscsi_adapter.
    storage_kind is 'VirtualDisk' (a logical volume created with
    hmc_create_virtual_disk) or 'PhysicalVolume' (a whole hdisk). storage_name
    is the DiskName / device name. target_device optionally pins the vtscsi
    device name on the VIOS.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            vios_uuid = await _resolve_vios_uuid(hmc, vios_name_or_uuid)
            lpar_uuid = await _resolve_lpar_uuid(hmc, lpar_name_or_uuid)
            return await hmc.map_storage_to_lpar(
                vios_uuid, storage_kind, storage_name, lpar_uuid, target_device
            )

    return _run(_go)




@mcp.tool
def hmc_create_media_repository(
    vios_name_or_uuid: str, vg_uuid: str, size_mb: int, profile: str | None = None
) -> dict[str, Any] | None:
    """Create the Virtual Media Repository (named VMLibrary) on a Volume Group.

    vios_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_vios).
    The repository holds file-backed ISO images for client partitions; only one
    can exist per VIOS. size_mb is RepositorySize.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            vios_uuid = await _resolve_vios_uuid(hmc, vios_name_or_uuid)
            return await hmc.create_media_repository(vios_uuid, vg_uuid, size_mb)

    return _run(_go)


@mcp.tool
def hmc_create_optical_media(
    vios_name_or_uuid: str, vg_uuid: str, media_name: str, size_mb: int, profile: str | None = None
) -> dict[str, Any] | None:
    """Create a blank VirtualOpticalMedia (ISO container) in the media repository.

    vios_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_vios).
    Only blank media can be created via the API; media_name is the file name
    (e.g. 'aix.iso'), size_mb is MediaSize.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            vios_uuid = await _resolve_vios_uuid(hmc, vios_name_or_uuid)
            return await hmc.create_optical_media(vios_uuid, vg_uuid, media_name, size_mb)

    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_delete_media_repository(vios_name_or_uuid: str, vg_uuid: str, profile: str | None = None) -> str:
    """Delete the Virtual Media Repository from a Volume Group.

    vios_name_or_uuid: accepts either a PartitionName or a UUID
    (find it with hmc_vios).
    This is an immediate (synchronous) delete — it returns a confirmation
    string once the HMC has applied the change; there is no job to poll.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            vios_uuid = await _resolve_vios_uuid(hmc, vios_name_or_uuid)
            await hmc.delete_media_repository(vios_uuid, vg_uuid)
        return f"Deleted media repository from VolumeGroup {vg_uuid}"

    return _run(_go)




@mcp.tool(annotations=_READ_ONLY)
def hmc_list_clusters(profile: str | None = None) -> list[dict[str, Any]]:
    """List Clusters (sets of VIOS nodes sharing a storage pool)."""

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.list_clusters()

    return _run(_go)


@mcp.tool(annotations=_READ_ONLY)
def hmc_shared_storage_pools(ssp_uuid: str | None = None, profile: str | None = None) -> Any:
    """List Shared Storage Pools or get one by UUID.

    When ssp_uuid is omitted, returns a list of all Shared Storage Pools
    (capacity, free space, logical units).

    When ssp_uuid is provided, returns the full details dict for that one
    pool (physical volumes, logical units), or None if not found.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            if ssp_uuid is not None:
                return await hmc.get_shared_storage_pool(ssp_uuid)
            return await hmc.list_shared_storage_pools()

    return _run(_go)


async def _lu_op(hmc, submit_fn, wait: bool, timeout_seconds: int, poll_interval: int) -> dict[str, Any] | None:
    """Submit a logical-unit job on an already-open *hmc* client; optionally wait for terminal state."""
    job = await submit_fn(hmc)
    if not wait or job is None:
        return job
    job_uuid = job.get("UUID") or (job.get("Resource") or {}).get("JobID")
    if not job_uuid:
        return job
    return await hmc.wait_for_job(
        job_uuid, timeout_seconds, poll_interval, job_href=job.get("link")
    )


@mcp.tool
def hmc_create_logical_unit(
    cluster_uuid: str,
    lu_name: str,
    lu_size_gb: int,
    lu_type: str = "THIN",
    device_type: str = "VirtualIO_Disk",
    cloned_from: str | None = None,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Create a Logical Unit (file-backed disk) in a Cluster/SSP.

    Submits a CreateLogicalUnit job and returns it — poll hmc_get_job for
    status; the result holds the new LU's UDID in LUCreated. lu_type is THIN
    or THICK; device_type is VirtualIO_Disk or VirtualIO_Image. cloned_from is
    an optional source LU UDID to clone. Find cluster_uuid with
    hmc_list_clusters.

    Set wait=True to block until the job reaches a terminal state.
    """
    async def _go():
        async with client_from_env(profile) as hmc:
            return await _lu_op(
                hmc,
                lambda hmc2: hmc2.create_logical_unit(
                    cluster_uuid, lu_name, lu_size_gb, lu_type, device_type, cloned_from
                ),
                wait, timeout_seconds, poll_interval,
            )
    return _run(_go)


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_delete_logical_unit(
    cluster_uuid: str,
    lu_udid: str,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Delete a Logical Unit from a Cluster/SSP by its UDID.

    Submits a DeleteLogicalUnit job and returns it — poll hmc_get_job for
    status (an asynchronous delete, unlike the immediate delete tools).

    Set wait=True to block until the job reaches a terminal state.
    """
    async def _go():
        async with client_from_env(profile) as hmc:
            return await _lu_op(
                hmc,
                lambda hmc2: hmc2.delete_logical_unit(cluster_uuid, lu_udid),
                wait, timeout_seconds, poll_interval,
            )
    return _run(_go)
