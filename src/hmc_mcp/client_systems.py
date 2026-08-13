"""HMCClient systems mixin.

The full client is assembled in :mod:`hmc_mcp.client` by inheriting every
domain mixin; this module only defines methods for systems.
"""

from __future__ import annotations

from typing import Any

from .client_parse import _parse_feed
from .jobs import (
    power_off_system_job,
    power_off_vios_job,
    power_on_system_job,
    power_on_vios_job,
)


class SystemsMixin:
    # -- Convenience wrappers for the common resources ----------------- #
    async def get_console_info(self) -> dict[str, Any] | None:
        """ManagementConsole: HMC version, network info, links to systems."""
        # Some HMC firmware builds return HTTP 500 on the unfiltered
        # ManagementConsole feed due to a null SessionId in the response XML.
        # Catch the error and return None instead of raising — the caller can
        # treat None as "HMC is reachable but console info is unavailable".
        try:
            entries = await self.list_uom("ManagementConsole")
            return entries[0] if entries else None
        except Exception:
            return None

    async def list_managed_systems(self) -> list[dict[str, Any]]:
        # Some HMC firmware builds return HTTP 500 on the unfiltered
        # ManagedSystem feed due to a null UUID in VirtualPersistentMemoryVolume
        # ("Nested path contains null property …/VirtualPersistentMemoryVolume/…").
        # Return an empty list only for that specific HMC firmware bug; re-raise
        # everything else (auth errors, parse errors, other HMC errors, etc.).
        try:
            return await self.list_uom("ManagedSystem")
        except Exception as exc:
            from .errors import HMCError
            if (
                isinstance(exc, HMCError)
                and exc.status_code == 500
                and "VirtualPersistentMemoryVolume" in str(exc)
            ):
                return []
            raise

    async def get_managed_system(self, uuid: str) -> dict[str, Any] | None:
        return await self.get_uom("ManagedSystem", uuid)

    async def find_system_by_name(self, name: str) -> dict[str, Any] | None:
        """Find a managed system by its SystemName (exact match)."""
        results = await self.search_uom("ManagedSystem", "SystemName", name)
        return results[0] if results else None

    async def modify_managed_system(
        self, system_uuid: str, system_xml: str
    ) -> dict[str, Any] | None:
        """Modify a managed system's properties (POST a partial ManagedSystem doc).

        Supported fields include system name, power-off policy, LPAR start
        policy, pending memory region size, huge pages, and mirroring mode.
        See documents.build_managed_system_document for the document builder.
        """
        path = f"/rest/api/uom/ManagedSystem/{system_uuid}"
        xml = await self._post(path, system_xml, resource_type="ManagedSystem")
        entries = _parse_feed(xml, path) if xml else []
        return entries[0] if entries else None

    # ------------------------------------------------------------------ #
    # Managed-system / VIOS power jobs
    # ------------------------------------------------------------------ #
    async def power_on_system(self, system_uuid: str) -> dict[str, Any] | None:
        """Power on a managed system (PowerOn job)."""

        return await self.submit_job(
            f"/rest/api/uom/ManagedSystem/{system_uuid}/do/PowerOn", power_on_system_job()
        )

    async def power_off_system(self, system_uuid: str, immediate: bool = False) -> dict[str, Any] | None:
        """Power off a managed system (PowerOff job; immediate skips graceful shutdown)."""

        return await self.submit_job(
            f"/rest/api/uom/ManagedSystem/{system_uuid}/do/PowerOff", power_off_system_job(immediate)
        )

    async def find_vios_by_name(self, name: str) -> dict[str, Any] | None:
        """Find a Virtual I/O Server by its PartitionName (exact match)."""
        results = await self.search_uom("VirtualIOServer", "PartitionName", name)
        return results[0] if results else None

    async def power_on_vios(self, vios_uuid: str) -> dict[str, Any] | None:
        """Power on a VIOS (PowerOn job)."""

        return await self.submit_job(
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}/do/PowerOn", power_on_vios_job()
        )

    async def power_off_vios(self, vios_uuid: str, immediate: bool = False) -> dict[str, Any] | None:
        """Power off a VIOS (PowerOff job; immediate skips graceful shutdown)."""

        return await self.submit_job(
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}/do/PowerOff", power_off_vios_job(immediate)
        )

    async def list_vios(self, system_uuid: str | None = None) -> list[dict[str, Any]]:
        if system_uuid:
            path = f"/rest/api/uom/ManagedSystem/{system_uuid}/VirtualIOServer"
            xml = await self._get(path, "VirtualIOServer")
            return _parse_feed(xml, path) if xml else []
        return await self.list_uom("VirtualIOServer")

    async def get_vios_storage_detail(self, vios_uuid: str) -> dict[str, Any] | None:
        """GET VirtualIOServer storage-detail group (device mappings).

        Fetches /rest/api/uom/VirtualIOServer/{vios_uuid}?group=ViosStorageDetail
        and returns the parsed entry, which includes VirtualSCSIMappings and
        VirtualFibreChannelMappings populated with physical/virtual device info.
        """
        path = f"/rest/api/uom/VirtualIOServer/{vios_uuid}?group=ViosStorageDetail"
        xml = await self._get(path, "VirtualIOServer")
        if not xml:
            return None
        entries = _parse_feed(xml, path)
        return entries[0] if entries else None
