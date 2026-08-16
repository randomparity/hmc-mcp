"""Presentation-neutral VIOS storage operations."""

from __future__ import annotations

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
