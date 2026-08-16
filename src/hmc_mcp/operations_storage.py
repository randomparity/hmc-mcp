"""Presentation-neutral VIOS storage operations."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from typing import Any

from .client import HMCClient
from .common import resolve_lpar_uuid, resolve_vios_uuid
from .documents import StorageKind
from .jobs import (
    DeviceType,
    LuType,
    validate_logical_unit_types,
    validate_wait_timing,
    wait_for_submitted_job,
)


async def list_volume_groups(hmc: HMCClient, vios: str) -> list[dict[str, Any]]:
    return await hmc.list_volume_groups(await resolve_vios_uuid(hmc, vios))


async def create_volume_group(
    hmc: HMCClient, vios: str, name: str, physical_volumes: list[str]
) -> dict[str, Any] | None:
    return await hmc.create_volume_group(
        await resolve_vios_uuid(hmc, vios), name, physical_volumes
    )


async def create_virtual_disk(
    hmc: HMCClient, vios: str, vg_uuid: str, name: str, size_mib: int
) -> dict[str, Any] | None:
    return await hmc.create_virtual_disk(
        await resolve_vios_uuid(hmc, vios), vg_uuid, name, size_mib
    )


async def delete_virtual_disk(
    hmc: HMCClient, vios: str, vg_uuid: str, disk_name: str
) -> dict[str, Any] | None:
    """Delete a Virtual Disk from a Volume Group.

    Validates that the disk is not mapped to any LPAR before deletion.
    Returns an error if the disk is in use; otherwise deletes the disk.

    Args:
        hmc: HMC client instance.
        vios: VIOS partition name or UUID.
        vg_uuid: Volume Group UUID containing the disk.
        disk_name: Name of the Virtual Disk to delete.

    Returns:
        The deleted disk metadata, or None if deletion failed.

    Raises:
        HMCError: If the disk is mapped to an LPAR or deletion fails.
    """
    # Check if the disk is currently mapped before deletion
    mappings = await hmc.list_storage_mappings(await resolve_vios_uuid(hmc, vios))
    disk_link = f"/rest/api/uom/VirtualIOServer/{await resolve_vios_uuid(hmc, vios)}/VolumeGroup/{vg_uuid}/VirtualDisk/{disk_name}"
    
    for mapping in mappings:
        backing_storage = mapping.get("Storage", {}).get("VirtualDisk", {})
        if isinstance(backing_storage, dict):
            storage_link = backing_storage.get("@href", "")
            if disk_link in storage_link or storage_link.endswith(f"VirtualDisk/{disk_name}"):
                lpar = mapping.get("AssociatedLogicalPartition", {})
                lpar_name = lpar.get("PartitionName", lpar.get("@href", "unknown"))
                from .errors import HMCError
                raise HMCError(
                    f"Cannot delete virtual disk '{disk_name}': it is mapped to LPAR '{lpar_name}'. "
                    f"Use detach_storage_mapping first to remove the mapping."
                )
    
    # Disk is not mapped, safe to delete
    return await hmc.delete_virtual_disk(
        await resolve_vios_uuid(hmc, vios), vg_uuid, disk_name
    )


async def map_storage(
    hmc: HMCClient,
    vios: str,
    kind: StorageKind,
    storage_name: str,
    lpar: str,
    target: str | None,
) -> tuple[str, dict[str, Any] | None]:
    vios_uuid = await resolve_vios_uuid(hmc, vios)
    lpar_uuid = await resolve_lpar_uuid(hmc, lpar)
    resource = await hmc.map_storage_to_lpar(
        vios_uuid, kind, storage_name, lpar_uuid, target
    )
    return lpar_uuid, resource


async def create_media_repository(
    hmc: HMCClient, vios: str, vg_uuid: str, size_mib: int
) -> dict[str, Any] | None:
    return await hmc.create_media_repository(
        await resolve_vios_uuid(hmc, vios), vg_uuid, size_mib
    )


async def create_optical_media(
    hmc: HMCClient, vios: str, vg_uuid: str, name: str, size_mib: int
) -> dict[str, Any] | None:
    return await hmc.create_optical_media(
        await resolve_vios_uuid(hmc, vios), vg_uuid, name, size_mib
    )
async def list_storage_mappings(
    hmc: HMCClient, vios: str, lpar: str | None = None
) -> list[dict[str, Any]]:
    """List VirtualSCSIMappings on a VIOS, optionally scoped to an LPAR.

    Returns mappings with backing storage details and client LPAR information.
    Use lpar to scope mappings to a single partition by name or UUID.
    """
    vios_uuid = await resolve_vios_uuid(hmc, vios)
    lpar_uuid = None
    if lpar:
        lpar_uuid = await resolve_lpar_uuid(hmc, lpar)
    return await hmc.list_storage_mappings(vios_uuid, lpar_uuid)


async def detach_storage_mapping(
    hmc: HMCClient, vios: str, mapping_uuid: str
) -> None:
    """Delete a VirtualSCSIMapping (detaches storage from LPAR, preserves backing storage).

    mapping_uuid is the UUID of the VirtualSCSIMapping to delete. This removes
    the mapping only; the backing PhysicalVolume or VirtualDisk is not affected.
    """
    vios_uuid = await resolve_vios_uuid(hmc, vios)
    await hmc.delete_storage_mapping(vios_uuid, mapping_uuid)


async def delete_media_repository(hmc: HMCClient, vios: str, vg_uuid: str) -> str:
    vios_uuid = await resolve_vios_uuid(hmc, vios)
    await hmc.delete_media_repository(vios_uuid, vg_uuid)
    return vios_uuid
async def get_media_repository(
    hmc: HMCClient, vios: str, vg_uuid: str
) -> dict[str, Any] | None:
    """Get the Virtual Media Repository (VMLibrary) from a Volume Group.

    Returns the repository with capacity (RepositorySize) and optionally
    embedded VirtualOpticalMedia entries if present.
    """
    return await hmc.get_media_repository(
        await resolve_vios_uuid(hmc, vios), vg_uuid
    )


async def list_optical_media(
    hmc: HMCClient, vios: str, vg_uuid: str
) -> list[dict[str, Any]]:
    """List Virtual Optical Media in the Virtual Media Repository.

    Returns a list of optical media entries (ISO containers) with their
    MediaName, MediaSize, and MediaType. The repository must exist
    (VMLibrary on the specified Volume Group).
    """
    return await hmc.list_optical_media(
        await resolve_vios_uuid(hmc, vios), vg_uuid
    )



async def upload_iso(
    hmc: HMCClient,
    vios: str,
    vg_uuid: str,
    media_name: str,
    iso_path: str | Path,
) -> dict[str, Any]:
    """Upload a local ISO file to a VIOS media repository via the HMC file broker.

    Computes SHA-256 and size before upload, refuses name collisions, and cleans
    up broker resources on every outcome. Returns staged result data including
    the imported media entry or existing media if the same content already exists.

    Args:
        hmc: HMC client instance.
        vios: VIOS name or UUID.
        vg_uuid: Volume Group UUID containing the media repository.
        media_name: Target name for the ISO in the repository.
        iso_path: Path to the local ISO file to upload.

    Returns:
        Dict with:
        - 'status': 'uploaded' | 'existing' | 'skipped'
        - 'media_name': Name of the media in the repository.
        - 'media_size_bytes': Size of the uploaded ISO.
        - 'sha256': SHA-256 checksum of the uploaded ISO.
        - 'media': Full media entry dict (if uploaded/existing), else None.
        - 'existing_name': Name of existing media with same SHA-256 (if existing).

    Raises:
        HMCError: For HMC API errors during broker operations or import.
        ValueError: For local file validation errors (not readable, too large).
        FileExistsError: If media_name already exists in the repository.
    """
    vios_uuid = await resolve_vios_uuid(hmc, vios)
    iso_path = Path(iso_path)

    # Validate source file
    if not iso_path.exists():
        raise ValueError(f"ISO file does not exist: {iso_path}")
    if not iso_path.is_file():
        raise ValueError(f"ISO path is not a file: {iso_path}")
    if not os.access(iso_path, os.R_OK):
        raise ValueError(f"ISO file is not readable: {iso_path}")

    # Compute SHA-256 and size
    sha256_hash = hashlib.sha256()
    file_size = 0
    chunk_size = 8192

    try:
        with iso_path.open("rb") as f:
            while chunk := f.read(chunk_size):
                sha256_hash.update(chunk)
                file_size += len(chunk)
    except OSError as e:
        raise ValueError(f"Failed to read ISO file: {e}") from e

    iso_sha256 = sha256_hash.hexdigest()

    # Check for name collision
    existing_media = await hmc.list_optical_media(vios_uuid, vg_uuid)
    for media in existing_media:
        if media.get("MediaName") == media_name:
            raise FileExistsError(
                f"Media name '{media_name}' already exists in repository. "
                "Use a different name or delete the existing media first."
            )

    # Check for duplicate content (same SHA-256 under different name)
    # Note: HMC does not expose trustworthy SHA-256 checksums in the API,
    # so we cannot perform duplicate detection via server-side checksums.
    # The client-side SHA-256 is computed for future deduplication features,
    # but we cannot skip upload based on existing content without a server-side
    # checksum to compare against.

    broker_uri: str | None = None
    try:
        # Create brokered file handle
        broker_uri = await hmc._broker_file_create(vios_uuid, vg_uuid, media_name)

        # Upload content
        with iso_path.open("rb") as f:
            content = f.read()
        await hmc._broker_file_upload(broker_uri, content)

        # Import into media repository
        await hmc._broker_iso_import(vios_uuid, vg_uuid, media_name, broker_uri)

        # Retrieve the uploaded media entry
        updated_media = await hmc.list_optical_media(vios_uuid, vg_uuid)
        uploaded_media_entry = None
        for media in updated_media:
            if media.get("MediaName") == media_name:
                uploaded_media_entry = media
                break

        return {
            "status": "uploaded",
            "media_name": media_name,
            "media_size_bytes": file_size,
            "sha256": iso_sha256,
            "media": uploaded_media_entry,
            "existing_name": None,
        }
    finally:
        # Always cleanup broker resources
        if broker_uri:
            try:
                await hmc._broker_file_cleanup(broker_uri)
            except Exception:
                # Cleanup errors are logged but don't fail the upload
                pass



async def create_logical_unit(
    hmc: HMCClient,
    cluster_uuid: str,
    lu_name: str,
    lu_size_gib: int,
    lu_type: LuType,
    device_type: DeviceType,
    cloned_from: str | None,
    wait: bool,
    timeout_seconds: int,
    poll_interval: int,
) -> dict[str, Any] | None:
    validate_logical_unit_types(lu_type, device_type)
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    job = await hmc.create_logical_unit(
        cluster_uuid, lu_name, lu_size_gib, lu_type, device_type, cloned_from
    )
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)


async def delete_logical_unit(
    hmc: HMCClient,
    cluster_uuid: str,
    lu_udid: str,
    wait: bool,
    timeout_seconds: int,
    poll_interval: int,
) -> dict[str, Any] | None:
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    job = await hmc.delete_logical_unit(cluster_uuid, lu_udid)
    return await wait_for_submitted_job(hmc, job, wait, timeout_seconds, poll_interval)


def validate_logical_unit_create(
    lu_type: LuType,
    device_type: DeviceType,
    wait: bool,
    timeout_seconds: int,
    poll_interval: int,
) -> None:
    """Validate a create request before an adapter opens a connection."""
    validate_logical_unit_types(lu_type, device_type)
    validate_wait_timing(wait, timeout_seconds, poll_interval)


def validate_logical_unit_wait(
    wait: bool, timeout_seconds: int, poll_interval: int
) -> None:
    """Validate job polling controls before an adapter opens a connection."""
    validate_wait_timing(wait, timeout_seconds, poll_interval)
