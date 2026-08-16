"""HMCClient storage mixin.

The full client is assembled in :mod:`hmc_mcp.client` by inheriting every
domain mixin; this module only defines methods for storage.
"""

from __future__ import annotations

from typing import Any

from .client_contracts import StorageClient
from .client_parse import _parse_feed
from .errors import HMCError
from .documents import (
    StorageKind,
    build_media_repository_delete_document,
    build_media_repository_document,
    build_virtual_disk_delete_document,
    build_virtual_disk_document,
    build_virtual_optical_mapping_document,
    build_virtual_optical_media_delete_document,
    build_virtual_optical_media_document,
    build_volume_group_document,
    build_vscsi_mapping_document,
)


class StorageMixin:
    # ------------------------------------------------------------------ #
    # Virtual storage (children of VirtualIOServer)
    # ------------------------------------------------------------------ #
    def get_lpar_link(self: StorageClient, lpar_uuid: str) -> str:
        """Atom SELF href for an LPAR (used when building mappings)."""
        return f"{self.config.base_url}/rest/api/uom/LogicalPartition/{lpar_uuid}"

    async def list_volume_groups(
        self: StorageClient, vios_uuid: str
    ) -> list[dict[str, Any]]:
        """List Volume Groups on a VIOS (free space, PVs, virtual disks).

        The VolumeGroup endpoint returns HTTP 204 when X-HMC-Schema-Version is
        present, so we deliberately omit the schema-version header here.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup"
        xml = await self._get(path, "VolumeGroup", include_schema_version=False)
        return _parse_feed(xml, path) if xml else []

    async def get_volume_group(
        self: StorageClient, vios_uuid: str, vg_uuid: str
    ) -> dict[str, Any] | None:
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        xml = await self._get(path, "VolumeGroup", include_schema_version=False)
        if not xml:
            return None
        entries = _parse_feed(xml, path)
        return entries[0] if entries else None

    async def create_volume_group(
        self: StorageClient,
        vios_uuid: str,
        name: str,
        physical_volumes: list[str],
    ) -> dict[str, Any] | None:
        """Create a Volume Group on a VIOS from physical volumes (e.g. ['hdisk10'])."""

        xml = build_volume_group_document(name, physical_volumes)
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup"
        resp = await self._put(path, xml, resource_type="VolumeGroup")
        entries = _parse_feed(resp, path) if resp else []
        return entries[0] if entries else None

    async def create_virtual_disk(
        self: StorageClient,
        vios_uuid: str,
        vg_uuid: str,
        disk_name: str,
        capacity_mib: int,
    ) -> dict[str, Any] | None:
        """Create a Virtual Disk (logical volume) in a Volume Group.

        The VolumeGroup POST endpoint returns HTTP 406 when X-HMC-Schema-Version
        is present on some HMC firmware (same behaviour as the GET), so we omit
        the schema-version header here.
        """

        xml = build_virtual_disk_document(disk_name, capacity_mib)
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        resp = await self._post(
            path, xml, resource_type="VolumeGroup", include_schema_version=False
        )
        entries = _parse_feed(resp, path) if resp else []
        return entries[0] if entries else None

    async def delete_virtual_disk(
        self: StorageClient, vios_uuid: str, vg_uuid: str, disk_name: str
    ) -> dict[str, Any] | None:
        """Delete a Virtual Disk (logical volume) from a Volume Group.

        The VolumeGroup POST endpoint returns HTTP 406 when X-HMC-Schema-Version
        is present on some HMC firmware (same behaviour as the GET), so we omit
        the schema-version header here.
        """

        xml = build_virtual_disk_delete_document(disk_name)
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        resp = await self._post(
            path, xml, resource_type="VolumeGroup", include_schema_version=False
        )
        entries = _parse_feed(resp, path) if resp else []
        return entries[0] if entries else None

    async def map_storage_to_lpar(
        self: StorageClient,
        vios_uuid: str,
        storage_kind: StorageKind,
        storage_name: str,
        lpar_uuid: str,
        target_device: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a VirtualSCSIMapping connecting backing storage to an LPAR.

        storage_kind is "PhysicalVolume" (whole hdisk) or "VirtualDisk" (a
        logical volume created with create_virtual_disk). storage_name is the
        device or disk name. lpar_uuid is the client partition to attach to.

        Omits X-HMC-Schema-Version for the same reason as the VolumeGroup
        endpoints — the schema-version header causes HTTP 406 on some firmware.
        """

        lpar_link = self.get_lpar_link(lpar_uuid)
        xml = build_vscsi_mapping_document(
            storage_kind, storage_name, lpar_link, target_device=target_device
        )
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}"
        resp = await self._post(
            path, xml, resource_type="VirtualIOServer", include_schema_version=False
        )
        entries = _parse_feed(resp, path) if resp else []
        return entries[0] if entries else None

    # ------------------------------------------------------------------ #
    # Storage Mapping Inventory and Detach
    # ------------------------------------------------------------------ #
    async def list_storage_mappings(
        self: StorageClient, vios_uuid: str, lpar_uuid: str | None = None
    ) -> list[dict[str, Any]]:
        """List VirtualSCSIMappings on a VIOS, optionally filtered by LPAR.

        Returns mappings with backing storage details (PhysicalVolume or VirtualDisk)
        and client LPAR information. Use lpar_uuid to scope mappings to a single LPAR.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}?group=ViosStorageDetail"
        xml = await self._get(path, "VirtualIOServer")
        if not xml:
            return []

        entries = _parse_feed(xml, path)
        if not entries:
            return []

        detail = entries[0]
        mappings = detail.get("VirtualSCSIMappings", {}).get("VirtualSCSIMapping", [])
        if not isinstance(mappings, list):
            mappings = [mappings] if mappings else []

        if lpar_uuid:
            expected_link = f"/rest/api/uom/LogicalPartition/{lpar_uuid}"
            mappings = [
                m for m in mappings
                if isinstance(m, dict) and m.get("AssociatedLogicalPartition", {}).get("@href") == expected_link
            ]

        return mappings if isinstance(mappings, list) else [mappings]

    async def delete_storage_mapping(
        self: StorageClient, vios_uuid: str, mapping_uuid: str
    ) -> None:
        """Delete a VirtualSCSIMapping by UUID (detaches storage from LPAR).

        This removes the mapping only; the backing storage (PhysicalVolume or
        VirtualDisk) is preserved.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VirtualSCSIMapping/{mapping_uuid}"
        await self._delete(path)

    # ------------------------------------------------------------------ #
    # Virtual Media Repository / Virtual Optical Media (VolumeGroup POSTs)
    # ------------------------------------------------------------------ #
    async def _post_volume_group_op(
        self: StorageClient, vios_uuid: str, vg_uuid: str, xml: str
    ) -> dict[str, Any] | None:
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        resp = await self._post(path, xml, resource_type="VolumeGroup")
        entries = _parse_feed(resp, path) if resp else []
        return entries[0] if entries else None

    async def create_media_repository(
        self: StorageClient, vios_uuid: str, vg_uuid: str, size_mib: int
    ) -> dict[str, Any] | None:
        """Create the Virtual Media Repository (named VMLibrary) on a Volume Group."""

        return await self._post_volume_group_op(
            vios_uuid, vg_uuid, build_media_repository_document(size_mib)
        )

    async def create_optical_media(
        self: StorageClient,
        vios_uuid: str,
        vg_uuid: str,
        media_name: str,
        size_mib: int,
    ) -> dict[str, Any] | None:
        """Create a blank VirtualOpticalMedia (ISO container) in the repository."""

        return await self._post_volume_group_op(
            vios_uuid,
            vg_uuid,
            build_virtual_optical_media_document(media_name, size_mib),
        )

    async def delete_media_repository(
        self: StorageClient, vios_uuid: str, vg_uuid: str
    ) -> dict[str, Any] | None:
        """Delete the Virtual Media Repository from a Volume Group."""

        return await self._post_volume_group_op(
            vios_uuid, vg_uuid, build_media_repository_delete_document()
        )

    async def delete_optical_media(
        self: StorageClient, vios_uuid: str, vg_uuid: str, media_name: str
    ) -> dict[str, Any] | None:
        """Delete a VirtualOpticalMedia (ISO image) from the media repository."""
        xml = build_virtual_optical_media_delete_document(media_name)
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        resp = await self._post(
            path, xml, resource_type="VolumeGroup", include_schema_version=False
        )
        entries = _parse_feed(resp, path) if resp else []
        return entries[0].get("Resource", entries[0]) if entries else None
    async def get_media_repository(
        self: StorageClient, vios_uuid: str, vg_uuid: str
    ) -> dict[str, Any] | None:
        """Get the Virtual Media Repository (VMLibrary) from a Volume Group.

        Returns the repository with capacity (RepositorySize) and optionally
        embedded VirtualOpticalMedia entries if present. Returns None if the
        Volume Group does not exist or has no media repository.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        try:
            xml = await self._get(path, "VolumeGroup", include_schema_version=False)
        except HMCError as exc:
            if exc.status_code == 404:
                return None
            raise
        if not xml:
            return None
        entries = _parse_feed(xml, path)
        return entries[0] if entries else None

    async def list_optical_media(
        self: StorageClient, vios_uuid: str, vg_uuid: str
    ) -> list[dict[str, Any]]:
        """List Virtual Optical Media in the Virtual Media Repository.

        Returns a list of optical media entries (ISO containers) with their
        MediaName, MediaSize, and MediaType. Returns empty list if the
        Volume Group does not exist or has no media repository.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VolumeGroup/{vg_uuid}"
        try:
            xml = await self._get(path, "VolumeGroup", include_schema_version=False)
        except HMCError as exc:
            if exc.status_code == 404:
                return []
            raise
        if not xml:
            return []

        entries = _parse_feed(xml, path)
        if not entries:
            return []

        # Extract VirtualOpticalMedia entries from the VolumeGroup response.
        # VirtualOpticalMedia are nested inside VirtualMediaRepository.
        optical_media: list[dict[str, Any]] = []
        for entry in entries:
            resource = entry.get("Resource", {})
            repo = resource.get("VirtualMediaRepository", {})
            media_list = repo.get("VirtualOpticalMedia", [])
            if isinstance(media_list, list):
                optical_media.extend(media_list)
            elif isinstance(media_list, dict):
                optical_media.append(media_list)

        return optical_media
    # ------------------------------------------------------------------ #
    # Virtual Optical Mapping (VirtualSCSIMapping for VirtualOpticalMedia)
    # ------------------------------------------------------------------ #
    async def list_optical_mappings(
        self: StorageClient, vios_uuid: str, lpar_uuid: str | None = None
    ) -> list[dict[str, Any]]:
        """List VirtualSCSIMappings for optical media on a VIOS, optionally filtered by LPAR.

        Returns only mappings that reference VirtualOpticalMedia backing, with media
        details and client LPAR information. Use lpar_uuid to scope mappings to a single LPAR.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}?group=ViosStorageDetail"
        xml = await self._get(path, "VirtualIOServer")
        if not xml:
            return []

        entries = _parse_feed(xml, path)
        if not entries:
            return []

        detail = entries[0]
        mappings = (
            detail.get("Resource", {})
            .get("VirtualSCSIMappings", {})
        )
        if not isinstance(mappings, dict):
            return []
        mappings = mappings.get("VirtualSCSIMapping", [])
        if not isinstance(mappings, list):
            mappings = [mappings] if mappings else []

        # Filter for optical mappings only (backed by VirtualOpticalMedia)
        optical_mappings = []
        for m in mappings:
            if not isinstance(m, dict):
                continue

            # Check if backed by VirtualOpticalMedia
            storage = m.get("Storage", {})
            if "VirtualOpticalMedia" in storage:
                optical_mappings.append(m)

        # Filter by LPAR if specified
        if lpar_uuid:
            expected_link = f"/rest/api/uom/LogicalPartition/{lpar_uuid}"
            optical_mappings = [
                m for m in optical_mappings
                if isinstance(m, dict) and m.get("AssociatedLogicalPartition", {}).get("@href") == expected_link
            ]

        return optical_mappings if isinstance(optical_mappings, list) else [optical_mappings]

    async def create_optical_mapping(
        self: StorageClient, vios_uuid: str, media_name: str, lpar_uuid: str,
        target_device: str | None = None
    ) -> dict[str, Any] | None:
        """Create a VirtualSCSIMapping for optical media (mount ISO to LPAR).

        Creates a read-only optical mapping from a VirtualOpticalMedia (ISO container)
        to a client LPAR. The media_name must exist in the VIOS media repository.
        Returns the created mapping resource.
        """
        xml = build_virtual_optical_mapping_document(
            media_name=media_name,
            lpar_link=self.get_lpar_link(lpar_uuid),
            target_device=target_device,
        )
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}"
        resp = await self._post(path, xml, resource_type="VirtualIOServer")
        entries = _parse_feed(resp, path) if resp else []
        return entries[0].get("Resource", entries[0]) if entries else None

    async def delete_optical_mapping(
        self: StorageClient, vios_uuid: str, mapping_uuid: str
    ) -> None:
        """Delete a VirtualSCSIMapping for optical media (unmount and detach).

        This removes the optical mapping only; the backing VirtualOpticalMedia
        (ISO container) is preserved and can be remounted later.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}/VirtualSCSIMapping/{mapping_uuid}"
        await self._delete(path)
