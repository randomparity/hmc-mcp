"""MCP tools for virtual adapters, virtual storage, media repository, and clusters/SSP.
"""

from __future__ import annotations

from typing import Any

from ._app import (
    _DESTRUCTIVE,
    _READ_ONLY,
    _run,
    mcp,
)

from .common import client_from_env



@mcp.tool(annotations=_READ_ONLY)
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


@mcp.tool(annotations=_DESTRUCTIVE)
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




@mcp.tool(annotations=_READ_ONLY)
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


@mcp.tool(annotations=_DESTRUCTIVE)
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




@mcp.tool(annotations=_READ_ONLY)
def hmc_list_clusters() -> list[dict[str, Any]]:
    """List Clusters (sets of VIOS nodes sharing a storage pool)."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_clusters()

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
def hmc_list_shared_storage_pools() -> list[dict[str, Any]]:
    """List Shared Storage Pools (capacity, free space, logical units)."""

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.list_shared_storage_pools()

    return _run(_go())


@mcp.tool(annotations=_READ_ONLY)
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


@mcp.tool(annotations=_DESTRUCTIVE)
def hmc_delete_logical_unit(cluster_uuid: str, lu_udid: str) -> dict[str, Any] | None:
    """Delete a Logical Unit from a Cluster/SSP by its UDID.

    Submits a DeleteLogicalUnit job and returns it — poll hmc_get_job for
    status (an asynchronous delete, unlike the immediate delete tools).
    """

    async def _go():
        async with client_from_env() as hmc:
            return await hmc.delete_logical_unit(cluster_uuid, lu_udid)

    return _run(_go())


