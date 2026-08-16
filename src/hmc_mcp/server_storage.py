"""MCP tools for virtual storage, media repositories, and clusters/SSP."""

from __future__ import annotations

from .tool_registry import tool_module

from typing import Any

from ._app import (
    _DESTRUCTIVE,
    _READ_ONLY,
    _run,
    _run_limited_collection,
)

from .common import client_from_env
from .documents import StorageKind
from .jobs import (
    DeviceType,
    LuType,
)
from .operations_storage import (
    create_logical_unit,
    create_media_repository,
    create_optical_media,
    create_virtual_disk,
    create_volume_group,
    delete_logical_unit,
    delete_media_repository,
    get_media_repository,
    list_optical_media,
    list_volume_groups,
    map_storage,
    validate_logical_unit_create,
    validate_logical_unit_wait,
)
from .operations_provision import (
    AttachDiskResult,
    ProvisionStorage,
    attach_disk_to_lpar,
)


tool, register_tools = tool_module()


@tool(annotations=_READ_ONLY)
def hmc_list_volume_groups(
    vios_name_or_uuid: str,
    profile: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """List Volume Groups on a VIOS.

    Each Volume Group shows free space (MiB), the physical volumes backing it
    and the virtual disks already carved out.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        profile: TOML profile name, or the environment-default HMC when omitted.
        limit: Maximum entries returned after the complete HMC feed is transferred
            and parsed; omitted returns all entries. This client-side cap does not
            reduce HMC work or network transfer.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await list_volume_groups(hmc, vios_name_or_uuid)

    return _run_limited_collection(_go, limit)


@tool
def hmc_create_volume_group(
    vios_name_or_uuid: str,
    name: str,
    physical_volumes: list[str],
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Create a Volume Group on a VIOS from one or more physical volumes.

    ``physical_volumes`` is a list of free PV device names (e.g. ['hdisk10']). Use
    the GetFreePhysicalVolumes job / VIOS 'lspv' to find unused disks. This
    pools the disks so virtual disks can be carved out for LPARs.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        name: New volume-group name.
        physical_volumes: One or more unused VIOS physical-volume device names.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await create_volume_group(
                hmc, vios_name_or_uuid, name, physical_volumes
            )

    return _run(_go)


@tool
def hmc_attach_disk_to_lpar(
    lpar_name_or_uuid: str,
    vios_uuid: str,
    vg_uuid: str,
    disk_name: str,
    capacity_mib: int,
    vios_partition_id: int,
    vios_slot: int,
    dry_run: bool = False,
    profile: str | None = None,
) -> AttachDiskResult:
    """Create and attach a virtual disk to an existing LPAR.

    Validates the LPAR and volume group before mutation. The execution order is
    create disk, add the paired vSCSI client adapter, then map the disk. Set
    dry_run=True to validate only. Expected HMC failures are returned per step;
    completed steps are ``ok`` and unattempted steps are ``skipped``.

    Args:
        lpar_name_or_uuid: Target partition name or UUID.
        vios_uuid: UUID of the VIOS that owns the volume group.
        vg_uuid: Volume-group UUID from ``hmc_list_volume_groups``.
        disk_name: Name for the new virtual disk.
        capacity_mib: New disk capacity in mebibytes.
        vios_partition_id: Numeric VIOS partition ID from ``hmc_list_vios``.
        vios_slot: Server-side virtual SCSI slot on the VIOS.
        dry_run: Validate all selectors and prerequisites without mutating the HMC.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await attach_disk_to_lpar(
                hmc,
                lpar_name_or_uuid,
                ProvisionStorage(vios_uuid, disk_name, vg_uuid=vg_uuid),
                capacity_mib=capacity_mib,
                vios_partition_id=vios_partition_id,
                vios_slot=vios_slot,
                dry_run=dry_run,
            )

    return _run(_go)


@tool
def hmc_create_virtual_disk(
    vios_name_or_uuid: str,
    vg_uuid: str,
    disk_name: str,
    capacity_mib: int,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Create a Virtual Disk (logical volume) inside a Volume Group.

    The disk becomes backing storage that you
    then attach to an LPAR with hmc_map_storage_to_lpar (storage_kind
    'VirtualDisk'). Find vg_uuid with hmc_list_volume_groups.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        vg_uuid: Volume-group UUID from ``hmc_list_volume_groups``.
        disk_name: Name for the new virtual disk.
        capacity_mib: New disk capacity in mebibytes.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await create_virtual_disk(
                hmc, vios_name_or_uuid, vg_uuid, disk_name, capacity_mib
            )

    return _run(_go)


@tool
def hmc_map_storage_to_lpar(
    vios_name_or_uuid: str,
    storage_name: str,
    lpar_name_or_uuid: str,
    storage_kind: StorageKind = "VirtualDisk",
    target_device: str | None = None,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Map backing storage to an LPAR via a Virtual SCSI mapping on a VIOS.

    The LPAR must already have a vSCSI adapter
    paired to this VIOS — see hmc_add_vscsi_adapter.
    ``storage_kind`` is 'VirtualDisk' (a logical volume created with
    hmc_create_virtual_disk) or 'PhysicalVolume' (a whole hdisk). storage_name
    is the DiskName / device name. target_device optionally pins the vtscsi
    device name on the VIOS.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        storage_name: Virtual-disk name or physical-volume device name.
        lpar_name_or_uuid: Target partition name or UUID from ``hmc_list_lpars``.
        storage_kind: ``VirtualDisk`` for a logical volume or ``PhysicalVolume``
            for a whole disk.
        target_device: Optional VIOS virtual-target-device name.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            _, resource = await map_storage(
                hmc,
                vios_name_or_uuid,
                storage_kind,
                storage_name,
                lpar_name_or_uuid,
                target_device,
            )
            return resource

    return _run(_go)


@tool
def hmc_create_media_repository(
    vios_name_or_uuid: str, vg_uuid: str, size_mib: int, profile: str | None = None
) -> dict[str, Any] | None:
    """Create the Virtual Media Repository (named VMLibrary) on a Volume Group.

    The repository holds file-backed ISO images for client partitions; only one
    can exist per VIOS. size_mib is RepositorySize measured in MiB.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        vg_uuid: Volume-group UUID from ``hmc_list_volume_groups``.
        size_mib: Repository capacity in mebibytes.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await create_media_repository(
                hmc, vios_name_or_uuid, vg_uuid, size_mib
            )

    return _run(_go)


@tool
def hmc_create_optical_media(
    vios_name_or_uuid: str,
    vg_uuid: str,
    media_name: str,
    size_mib: int,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Create a blank VirtualOpticalMedia (ISO container) in the media repository.

    Only blank media can be created via the API; media_name is the file name
    (e.g. 'aix.iso'), size_mib is MediaSize measured in MiB.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        vg_uuid: Volume-group UUID containing the media repository.
        media_name: ISO container file name, such as ``aix.iso``.
        size_mib: Blank media capacity in mebibytes.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await create_optical_media(
                hmc, vios_name_or_uuid, vg_uuid, media_name, size_mib
            )

    return _run(_go)


@tool(annotations=_DESTRUCTIVE)
def hmc_delete_media_repository(
    vios_name_or_uuid: str, vg_uuid: str, profile: str | None = None
) -> str:
    """Delete the Virtual Media Repository from a Volume Group.

    This is an immediate (synchronous) delete — it returns a confirmation
    string once the HMC has applied the change; there is no job to poll.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        vg_uuid: Volume-group UUID containing the repository.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            await delete_media_repository(hmc, vios_name_or_uuid, vg_uuid)
        return f"Deleted media repository from VolumeGroup {vg_uuid}"


    return _run(_go)


@tool(annotations=_READ_ONLY)
def hmc_get_media_repository(
    vios_name_or_uuid: str, vg_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Get the Virtual Media Repository (VMLibrary) from a Volume Group.

    Returns the repository with capacity (RepositorySize) and optionally
    embedded VirtualOpticalMedia entries if present.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        vg_uuid: Volume-group UUID containing the repository.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await get_media_repository(hmc, vios_name_or_uuid, vg_uuid)

    return _run(_go)


@tool(annotations=_READ_ONLY)
def hmc_list_optical_media(
    vios_name_or_uuid: str, vg_uuid: str, profile: str | None = None
) -> list[dict[str, Any]]:
    """List Virtual Optical Media in the Virtual Media Repository.

    Returns a list of optical media entries (ISO containers) with their
    MediaName, MediaSize, and MediaType. The repository must exist
    (VMLibrary on the specified Volume Group).

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        vg_uuid: Volume-group UUID containing the repository.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await list_optical_media(hmc, vios_name_or_uuid, vg_uuid)

    return _run(_go)

    return _run(_go)
@tool(annotations=_READ_ONLY)
def hmc_list_storage_mappings(
    vios_name_or_uuid: str,
    lpar_name_or_uuid: str | None = None,
    profile: str | None = None,
) -> list[dict[str, Any]]:
    """List VirtualSCSIMappings on a VIOS, optionally filtered by LPAR.

    Returns storage mappings with backing storage details (PhysicalVolume or VirtualDisk)
    and client LPAR information. Use lpar_name_or_uuid to scope mappings to a single LPAR.
    """
    async def _go() -> list[dict[str, Any]]:
        config = load_profile(profile)
        async with HMCClient(config) as hmc:
            from hmc_mcp.operations_storage import list_storage_mappings
            return await list_storage_mappings(hmc, vios_name_or_uuid, lpar_name_or_uuid)
    return _run(_go)


@tool(annotations=_DESTRUCTIVE)
def hmc_detach_storage_mapping(
    vios_name_or_uuid: str,
    mapping_uuid: str,
    profile: str | None = None,
) -> str:
    """Delete a VirtualSCSIMapping by UUID (detaches storage from LPAR).

    This removes the mapping only; the backing storage (PhysicalVolume or VirtualDisk)
    is preserved. mapping_uuid is the UUID of the VirtualSCSIMapping to delete.
    """
    async def _go() -> str:
        config = load_profile(profile)
        async with HMCClient(config) as hmc:
            from hmc_mcp.operations_storage import detach_storage_mapping
            await detach_storage_mapping(hmc, vios_name_or_uuid, mapping_uuid)
            return mapping_uuid
    return _run(_go)


@tool(annotations=_READ_ONLY)
def hmc_list_clusters(
    profile: str | None = None, limit: int | None = None
) -> list[dict[str, Any]]:
    """List Clusters (sets of VIOS nodes sharing a storage pool).

    Args:
        profile: TOML profile name, or the environment-default HMC when omitted.
        limit: Maximum entries returned after the complete HMC feed is transferred
            and parsed; omitted returns all entries. This client-side cap does not
            reduce HMC work or network transfer.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.list_clusters()

    return _run_limited_collection(_go, limit)


@tool(annotations=_READ_ONLY)
def hmc_list_shared_storage_pools(
    profile: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """List all Shared Storage Pools with capacity and logical-unit details.

    Args:
        profile: TOML profile name, or the environment-default HMC when omitted.
        limit: Maximum entries returned after the complete HMC feed is transferred
            and parsed; omitted returns all entries. This client-side cap does not
            reduce HMC work or network transfer.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.list_shared_storage_pools()

    return _run_limited_collection(_go, limit)


@tool(annotations=_READ_ONLY)
def hmc_get_shared_storage_pool(
    ssp_uuid: str, profile: str | None = None
) -> dict[str, Any] | None:
    """Get one Shared Storage Pool by UUID, or None for an empty response.

    Args:
        ssp_uuid: Shared-storage-pool UUID from ``hmc_list_shared_storage_pools``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await hmc.get_shared_storage_pool(ssp_uuid)

    return _run(_go)


@tool
def hmc_create_logical_unit(
    cluster_uuid: str,
    lu_name: str,
    lu_size_gib: int,
    lu_type: LuType = "THIN",
    device_type: DeviceType = "VirtualIO_Disk",
    cloned_from: str | None = None,
    wait: bool = False,
    timeout_seconds: int = 300,
    poll_interval: int = 5,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Create a Logical Unit (file-backed disk) in a Cluster/SSP.

    Submits a CreateLogicalUnit job and returns it — poll hmc_get_job for
    status; the result holds the new LU's UDID in LUCreated. lu_size_gib is the
    requested logical-unit size in GiB. lu_type is THIN
    or THICK; device_type is VirtualIO_Disk or VirtualIO_Image. cloned_from is
    an optional source LU UDID to clone. Find cluster_uuid with
    hmc_list_clusters.

    Set wait=True to block until the job reaches a terminal state.

    Args:
        cluster_uuid: Cluster UUID from ``hmc_list_clusters``.
        lu_name: Name for the new logical unit.
        lu_size_gib: Logical-unit capacity in gibibytes.
        lu_type: ``THIN`` for sparse allocation or ``THICK`` for full allocation.
        device_type: ``VirtualIO_Disk`` or ``VirtualIO_Image``.
        cloned_from: Optional source logical-unit UDID to clone.
        wait: Wait for the submitted job to reach a terminal state.
        timeout_seconds: Maximum wait duration in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    validate_logical_unit_create(
        lu_type, device_type, wait, timeout_seconds, poll_interval
    )

    async def _go():
        async with client_from_env(profile) as hmc:
            return await create_logical_unit(
                hmc,
                cluster_uuid,
                lu_name,
                lu_size_gib,
                lu_type,
                device_type,
                cloned_from,
                wait,
                timeout_seconds,
                poll_interval,
            )

    return _run(_go)


@tool(annotations=_DESTRUCTIVE)
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

    Args:
        cluster_uuid: Cluster UUID from ``hmc_list_clusters``.
        lu_udid: Logical-unit UDID to permanently delete.
        wait: Wait for the submitted job to reach a terminal state.
        timeout_seconds: Maximum wait duration in seconds.
        poll_interval: Seconds between job-status requests while waiting.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """
    validate_logical_unit_wait(wait, timeout_seconds, poll_interval)

    async def _go():
        async with client_from_env(profile) as hmc:
            return await delete_logical_unit(
                hmc,
                cluster_uuid,
                lu_udid,
                wait,
                timeout_seconds,
                poll_interval,
            )

    return _run(_go)
