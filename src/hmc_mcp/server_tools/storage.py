"""MCP tools for virtual storage, media repositories, and clusters/SSP."""

from __future__ import annotations

from ..tool_registry import tool_module

from typing import Any

from .._app import (
    _run,
    _run_limited_collection,
)

from ..client.client_factory import client_from_env
from ..documents import StorageKind
from ..jobs import (
    DeviceType,
    LuType,
)
from ..operations.storage import (
    create_logical_unit,
    create_media_repository,
    create_optical_media,
    create_virtual_disk,
    create_volume_group,
    delete_logical_unit,
    delete_media_repository,
    delete_optical_media,
    delete_virtual_disk,
    detach_storage_mapping,
    list_optical_mappings,
    mount_optical_media,
    unmount_optical_media,
    get_media_repository,
    list_optical_media,
    list_storage_mappings,
    list_volume_groups,
    map_storage,
    validate_logical_unit_create,
    upload_iso,
    validate_logical_unit_wait,
)
from ..operations.provision import (
    AttachDiskResult,
    ProvisionStorage,
    attach_disk_to_lpar,
)


tool, register_tools, tool_security = tool_module()


@tool(effect="read", operation="storage.list_volume_groups", target_kind="vios")
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


@tool(effect="mutate", operation="storage.create_volume_group", target_kind="vios")
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


# Not exhaustive for the same reason as the adapter pair, and one more: it
# declares both `vios_uuid` and `vios_partition_id` as `vios` selectors and
# nothing checks that they name the same VIOS, so the partition ID is a second,
# unverified identity the allowlist cannot bound.
@tool(
    effect="mutate",
    operation="storage.attach_disk",
    target_kind="lpar",
    exhaustive_targets=False,
)
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
    system_name_or_uuid: str | None = None,
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
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide.
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
                system_name_or_uuid=system_name_or_uuid,
            )

    return _run(_go)


@tool(effect="mutate", operation="storage.create_disk", target_kind="vios")
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


@tool(effect="destructive", operation="storage.delete_disk", target_kind="vios")
def hmc_delete_virtual_disk(
    vios_name_or_uuid: str,
    vg_uuid: str,
    disk_name: str,
    profile: str | None = None,
) -> dict[str, Any] | None:
    """Delete a Virtual Disk from a Volume Group.

    Validates that the disk is not mapped to any LPAR before deletion.
    Raises an error if the disk is in use; otherwise deletes the disk.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        vg_uuid: Volume-group UUID from ``hmc_list_volume_groups``.
        disk_name: Name of the Virtual Disk to delete.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await delete_virtual_disk(
                hmc, vios_name_or_uuid, vg_uuid, disk_name
            )

    return _run(_go)


@tool(effect="mutate", operation="storage.map", target_kind="vios")
def hmc_map_storage_to_lpar(
    vios_name_or_uuid: str,
    storage_name: str,
    lpar_name_or_uuid: str,
    storage_kind: StorageKind = "VirtualDisk",
    target_device: str | None = None,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
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
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await map_storage(
                hmc,
                vios_name_or_uuid,
                storage_kind,
                storage_name,
                lpar_name_or_uuid,
                target_device,
                system_name_or_uuid,
            )

    return _run(_go)


@tool(effect="mutate", operation="media.create_repository", target_kind="vios")
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


@tool(effect="mutate", operation="media.create", target_kind="vios")
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


@tool(effect="destructive", operation="media.delete_repository", target_kind="vios")
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

@tool(effect="destructive", operation="media.delete", target_kind="vios")
def hmc_delete_optical_media(
    vios_name_or_uuid: str,
    vg_uuid: str,
    media_name: str,
    profile: str | None = None,
) -> str:
    """Delete a VirtualOpticalMedia (ISO image) from the media repository.

    Refuses deletion if the media is currently mounted to any LPAR. Unmount
    all references first.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        vg_uuid: Volume-group UUID containing the repository.
        media_name: Name of the ISO image to delete.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            await delete_optical_media(hmc, vios_name_or_uuid, vg_uuid, media_name)
        return f"Deleted optical media '{media_name}' from VolumeGroup {vg_uuid}"

    return _run(_go)


@tool(effect="read", operation="media.get_repository", target_kind="vios")
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


@tool(effect="read", operation="media.list", target_kind="vios")
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

@tool(effect="read", operation="storage.list_mappings", target_kind="vios")
def hmc_list_storage_mappings(
    vios_name_or_uuid: str,
    lpar_name_or_uuid: str | None = None,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> list[dict[str, Any]]:
    """List VirtualSCSIMappings on a VIOS, optionally filtered by LPAR.

    Returns storage mappings with backing storage details (PhysicalVolume or
    VirtualDisk) and client LPAR information.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        lpar_name_or_uuid: Optional LPAR name or UUID to scope mappings to a
            single client LPAR.
        profile: TOML profile name, or the environment-default HMC when omitted.
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide.
    """

    async def _go():
        async with client_from_env(profile) as hmc:
            return await list_storage_mappings(
                hmc, vios_name_or_uuid, lpar_name_or_uuid, system_name_or_uuid
            )

    return _run(_go)


@tool(effect="destructive", operation="storage.detach_mapping", target_kind="vios")
def hmc_detach_storage_mapping(
    vios_name_or_uuid: str,
    mapping_uuid: str,
    profile: str | None = None,
) -> str:
    """Detach a VirtualSCSIMapping by its inventory UUID.

    Removes the mapping only; the backing storage (PhysicalVolume or
    VirtualDisk) is preserved.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        mapping_uuid: Exact UUID returned by ``hmc_list_storage_mappings``.
        profile: TOML profile name, or the environment-default HMC when omitted.
    """

    async def _go() -> str:
        async with client_from_env(profile) as hmc:
            await detach_storage_mapping(hmc, vios_name_or_uuid, mapping_uuid)
            return mapping_uuid

    return _run(_go)


@tool(effect="read", operation="cluster.list", target_kind="console")
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


@tool(effect="read", operation="cluster.list_pools", target_kind="console")
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


@tool(effect="read", operation="cluster.get_pool", target_kind="shared_storage_pool")
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


@tool(effect="mutate", operation="cluster.create_logical_unit", target_kind="cluster")
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


@tool(effect="destructive", operation="cluster.delete_logical_unit", target_kind="cluster")
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

@tool(effect="mutate", operation="media.upload_iso", target_kind="vios")
def hmc_upload_iso(
    vios_name_or_uuid: str,  # VIOS name or UUID to target
    vg_uuid: str,  # Volume Group UUID containing the media repository
    media_name: str,  # Target name for the ISO in the repository
    iso_source: str,  # http(s) URL to download the ISO from
    profile: str | None = None,  # HMC profile name (uses default if omitted)
) -> dict[str, Any]:
    """Upload an ISO to a VIOS media repository via the HMC file broker.

    The ISO is downloaded from an http(s) URL, which is the only accepted source:
    a path on the MCP server's filesystem is refused. The download runs from the
    MCP server's network position, so the URL's host must be on the operator's
    allowlist (`HMC_ISO_URL_ALLOWLIST`); with no allowlist configured every URL
    is refused, and redirects are never followed. Computes SHA-256 and size
    before upload, refuses name collisions, and cleans up broker resources on
    every outcome. Returns staged result data including the imported media entry.

    Args:
        vios_name_or_uuid: VIOS name or UUID to target.
        vg_uuid: Volume Group UUID containing the media repository.
        media_name: Target name for the ISO in the repository.
        iso_source: http(s) URL to download the ISO from, served by a host on
            the operator's `HMC_ISO_URL_ALLOWLIST`. Local filesystem paths are
            not accepted, and the ISO must be served at this URL rather than
            redirected from it.
        profile: HMC profile name (uses default if omitted).

    Returns:
        Dict with upload status, media details, SHA-256 checksum, and size.

    Raises:
        HMCError: For HMC API errors during broker operations or import.
        ValueError: If iso_source is not an http(s) URL, if its host is not on
            the operator's allowlist, if the server answers with a redirect, or
            if the download exceeds the size bound.
        FileExistsError: If media_name already exists in the repository.
    """
    async def _go():
        async with client_from_env(profile) as hmc:
            return await upload_iso(hmc, vios_name_or_uuid, vg_uuid, media_name, iso_source)

    return _run(_go)


@tool(effect="read", operation="media.list_mappings", target_kind="vios")
def hmc_list_optical_mappings(
    vios_name_or_uuid: str,
    lpar_name_or_uuid: str | None = None,
    profile: str | None = None,
    limit: int | None = None,
    system_name_or_uuid: str | None = None,
) -> list[dict[str, Any]]:
    """List VirtualSCSIMappings for optical media on a VIOS, optionally filtered by LPAR.

    Returns only mappings that reference VirtualOpticalMedia backing, with media
    details and client LPAR information.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        lpar_name_or_uuid: Optional LPAR name or UUID to scope mappings to a
            single client LPAR.
        profile: TOML profile name, or the environment-default HMC when omitted.
        limit: Maximum entries returned after the complete HMC feed is transferred
            and parsed; omitted returns all entries.
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide.
    """
    async def _go():
        async with client_from_env(profile) as hmc:
            mappings = await list_optical_mappings(
                hmc, vios_name_or_uuid, lpar_name_or_uuid, system_name_or_uuid
            )
            return mappings if limit is None else mappings[:limit]
    return _run(_go)


@tool(effect="mutate", operation="media.mount", target_kind="vios")
def hmc_mount_optical_media(
    vios_name_or_uuid: str,
    media_name: str,
    lpar_name_or_uuid: str,
    target_device: str | None = None,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Create a VirtualSCSIMapping for optical media (mount ISO to LPAR).

    Creates a read-only optical mapping from a VirtualOpticalMedia (ISO container)
    to a client LPAR. The media_name must exist in the VIOS media repository.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        media_name: Name of the VirtualOpticalMedia (ISO) in the repository.
        lpar_name_or_uuid: LPAR name or UUID to mount the media to.
        target_device: Optional vtscsi target device name to pin the mapping.
        profile: TOML profile name, or the environment-default HMC when omitted.
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide.
    """
    async def _go():
        async with client_from_env(profile) as hmc:
            return await mount_optical_media(
                hmc,
                vios_name_or_uuid,
                media_name,
                lpar_name_or_uuid,
                target_device,
                system_name_or_uuid,
            )
    return _run(_go)


@tool(effect="destructive", operation="media.unmount", target_kind="vios")
def hmc_unmount_optical_media(
    vios_name_or_uuid: str,
    lpar_name_or_uuid: str,
    media_name: str,
    profile: str | None = None,
    system_name_or_uuid: str | None = None,
) -> str:
    """Remove a VirtualSCSIMapping for optical media (unmount).

    Identifies the mapping by the LPAR and media name rather than a UUID.
    Uses a read-modify-write pattern against the full VirtualIOServer document.
    Removes the optical mapping only; the backing VirtualOpticalMedia (ISO
    container) is preserved and can be remounted later. No unload-without-detach
    path has been identified on the surveyed firmware, so this is also how you
    detach an optical mapping.

    The success message is not evidence that a mapping was removed. A media_name
    matching no mapping for that LPAR is a silent no-op that still reports
    success, and a partial one can remove a different mapping (see media_name
    below). ADR 0079 records both as rejected for VirtualSCSIMapping removal —
    it rejects silent success, and it rejects selecting by LPAR and backing-
    storage name, which is exactly this tool's selector. That is a settled
    decision this operation has not caught up with, not an open question; #439
    owns the reconciliation.

    To verify, capture ``hmc_list_storage_mappings`` before the call and diff it
    after, then act on the result — especially before rebooting the partition.
    ``hmc_list_optical_mappings`` alone is not enough: it returns only
    VirtualOpticalMedia-backed mappings, so a wrongly removed disk mapping is
    invisible to it and reads identically to a no-op. Where an access policy
    withholds the read-effect inventory tools, this tool's success message
    carries no evidence at all.

    The read-modify-write also rewrites the whole VirtualIOServer document from
    a GET snapshot, so another writer's change in that window is lost; ADR 0079
    requires callers to serialize concurrent VIOS mapping changes.

    Args:
        vios_name_or_uuid: VIOS partition name or UUID from ``hmc_list_vios``.
        lpar_name_or_uuid: LPAR name or UUID the media is mounted to.
        media_name: Exact ``MediaName`` of the VirtualOpticalMedia (ISO) to
            unmount, as reported by ``hmc_list_optical_mappings``. Selection is
            currently a substring match over the serialized mapping, so an empty
            or partial name can remove the wrong mapping — including a
            non-optical one such as a boot disk (#439). Do not construct this
            value; copy it from the inventory.
        profile: TOML profile name, or the environment-default HMC when omitted.
        system_name_or_uuid: Optional SystemName or UUID that disambiguates the
            partition name; when omitted the name is searched fleet-wide.
    """
    async def _go():
        async with client_from_env(profile) as hmc:
            await unmount_optical_media(
                hmc, vios_name_or_uuid, lpar_name_or_uuid, media_name, system_name_or_uuid
            )
            return f"Unmounted {media_name!r} from LPAR {lpar_name_or_uuid} on VIOS {vios_name_or_uuid}"
    return _run(_go)
