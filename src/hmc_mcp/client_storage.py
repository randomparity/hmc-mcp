"""HMCClient storage mixin.

The full client is assembled in :mod:`hmc_mcp.client` by inheriting every
domain mixin; this module only defines methods for storage.
"""

from __future__ import annotations

from typing import Any

from .client_parse import _parse_feed
from .documents import (
    build_media_repository_delete_document,
    build_media_repository_document,
    build_virtual_disk_document,
    build_virtual_optical_media_document,
    build_volume_group_document,
    build_vscsi_mapping_document,
)


class StorageMixin:
    # ------------------------------------------------------------------ #
    # Virtual storage (children of VirtualIOServer)
    # ------------------------------------------------------------------ #
    def get_lpar_link(self, lpar_uuid: str) -> str:
        """Atom SELF href for an LPAR (used when building mappings)."""
        return f"{self.config.base_url}/rest/api/uom/LogicalPartition/{lpar_uuid}"

    async def list_volume_groups(self, vios_uuid: str) -> list[dict[str, Any]]:
        """List Volume Groups on a VIOS (free space, PVs, virtual disks)."""
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup"
        xml = await self._get(path, "VolumeGroup")
        return _parse_feed(xml, path) if xml else []

    async def get_volume_group(self, vios_uuid: str, vg_uuid: str) -> dict[str, Any] | None:
        return await self.get_uom_path(
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}", "VolumeGroup"
        )

    async def create_volume_group(
        self, vios_uuid: str, name: str, physical_volumes: list[str]
    ) -> dict[str, Any] | None:
        """Create a Volume Group on a VIOS from physical volumes (e.g. ['hdisk10'])."""

        xml = build_volume_group_document(name, physical_volumes)
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup"
        resp = await self._put(path, xml, resource_type="VolumeGroup")
        entries = _parse_feed(resp, path) if resp else []
        return entries[0] if entries else None

    async def create_virtual_disk(
        self, vios_uuid: str, vg_uuid: str, disk_name: str, capacity_mb: int
    ) -> dict[str, Any] | None:
        """Create a Virtual Disk (logical volume) in a Volume Group."""

        xml = build_virtual_disk_document(disk_name, capacity_mb)
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        resp = await self._post(path, xml, resource_type="VolumeGroup")
        entries = _parse_feed(resp, path) if resp else []
        return entries[0] if entries else None

    async def map_storage_to_lpar(
        self,
        vios_uuid: str,
        storage_kind: str,
        storage_name: str,
        lpar_uuid: str,
        target_device: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a VirtualSCSIMapping connecting backing storage to an LPAR.

        storage_kind is "PhysicalVolume" (whole hdisk) or "VirtualDisk" (a
        logical volume created with create_virtual_disk). storage_name is the
        device or disk name. lpar_uuid is the client partition to attach to.
        """

        lpar_link = self.get_lpar_link(lpar_uuid)
        xml = build_vscsi_mapping_document(
            storage_kind, storage_name, lpar_link, target_device=target_device
        )
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}"
        resp = await self._post(path, xml, resource_type="VirtualIOServer")
        entries = _parse_feed(resp, path) if resp else []
        return entries[0] if entries else None

    # ------------------------------------------------------------------ #
    # Virtual Media Repository / Virtual Optical Media (VolumeGroup POSTs)
    # ------------------------------------------------------------------ #
    async def _post_volume_group_op(
        self, vios_uuid: str, vg_uuid: str, xml: str
    ) -> dict[str, Any] | None:
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        resp = await self._post(path, xml, resource_type="VolumeGroup")
        entries = _parse_feed(resp, path) if resp else []
        return entries[0] if entries else None

    async def create_media_repository(
        self, vios_uuid: str, vg_uuid: str, size_mb: int
    ) -> dict[str, Any] | None:
        """Create the Virtual Media Repository (named VMLibrary) on a Volume Group."""

        return await self._post_volume_group_op(
            vios_uuid, vg_uuid, build_media_repository_document(size_mb)
        )

    async def create_optical_media(
        self, vios_uuid: str, vg_uuid: str, media_name: str, size_mb: int
    ) -> dict[str, Any] | None:
        """Create a blank VirtualOpticalMedia (ISO container) in the repository."""

        return await self._post_volume_group_op(
            vios_uuid, vg_uuid, build_virtual_optical_media_document(media_name, size_mb)
        )

    async def delete_media_repository(self, vios_uuid: str, vg_uuid: str) -> dict[str, Any] | None:
        """Delete the Virtual Media Repository from a Volume Group."""

        return await self._post_volume_group_op(
            vios_uuid, vg_uuid, build_media_repository_delete_document()
        )
