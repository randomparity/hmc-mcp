"""Presentation-neutral VIOS storage operations."""

from __future__ import annotations

from typing import Any

from .client import HMCClient
from .common import resolve_lpar_uuid, resolve_vios_uuid
from .documents import StorageKind


async def list_volume_groups(hmc: HMCClient, vios: str) -> list[dict[str, Any]]:
    return await hmc.list_volume_groups(await resolve_vios_uuid(hmc, vios))


async def create_volume_group(
    hmc: HMCClient, vios: str, name: str, physical_volumes: list[str]
) -> dict[str, Any] | None:
    return await hmc.create_volume_group(
        await resolve_vios_uuid(hmc, vios), name, physical_volumes
    )


async def create_virtual_disk(
    hmc: HMCClient, vios: str, vg_uuid: str, name: str, size_mb: int
) -> dict[str, Any] | None:
    return await hmc.create_virtual_disk(
        await resolve_vios_uuid(hmc, vios), vg_uuid, name, size_mb
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
    hmc: HMCClient, vios: str, vg_uuid: str, size_mb: int
) -> dict[str, Any] | None:
    return await hmc.create_media_repository(
        await resolve_vios_uuid(hmc, vios), vg_uuid, size_mb
    )


async def create_optical_media(
    hmc: HMCClient, vios: str, vg_uuid: str, name: str, size_mb: int
) -> dict[str, Any] | None:
    return await hmc.create_optical_media(
        await resolve_vios_uuid(hmc, vios), vg_uuid, name, size_mb
    )


async def delete_media_repository(hmc: HMCClient, vios: str, vg_uuid: str) -> str:
    vios_uuid = await resolve_vios_uuid(hmc, vios)
    await hmc.delete_media_repository(vios_uuid, vg_uuid)
    return vios_uuid
