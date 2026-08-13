"""HMCClient lpars mixin.

The full client is assembled in :mod:`hmc_mcp.client` by inheriting every
domain mixin; this module only defines methods for lpars.
"""

from __future__ import annotations

from typing import Any

from .client_parse import _parse_feed


class LparsMixin:
    async def list_logical_partitions(self, system_uuid: str | None = None) -> list[dict[str, Any]]:
        if system_uuid:
            path = f"/rest/api/uom/ManagedSystem/{system_uuid}/LogicalPartition"
            xml = await self._get(path, "LogicalPartition")
            return _parse_feed(xml, path) if xml else []
        return await self.list_uom("LogicalPartition")

    async def get_logical_partition(self, uuid: str) -> dict[str, Any] | None:
        return await self.get_uom("LogicalPartition", uuid)

    async def find_partition_by_name(self, name: str) -> dict[str, Any] | None:
        results = await self.search_uom("LogicalPartition", "PartitionName", name)
        return results[0] if results else None

    async def create_logical_partition(
        self, system_uuid: str, lpar_xml: str
    ) -> dict[str, Any] | None:
        """Create an LPAR on a managed system.

        PUTs a LogicalPartition document (see documents.build_lpar_document)
        to /rest/api/uom/ManagedSystem/{system_uuid}/LogicalPartition and
        returns the created partition entry.

        Omits X-HMC-Schema-Version header — some HMC firmware versions return
        HTTP 406 for this PUT when the schema-version header is present.
        """
        path = f"/rest/api/uom/ManagedSystem/{system_uuid}/LogicalPartition"
        xml = await self._put(path, lpar_xml, resource_type="LogicalPartition",
                              include_schema_version=False)
        entries = _parse_feed(xml, path) if xml else []
        return entries[0] if entries else None

    async def modify_logical_partition(
        self, lpar_uuid: str, lpar_xml: str
    ) -> dict[str, Any] | None:
        """Modify an LPAR's properties (POST a partial LogicalPartition doc).

        Memory/CPU changes to a *running* partition only take effect if the
        partition supports dynamic LPAR (DLPAR) and RMC is up; otherwise the
        change lands in the profile for the next activation.
        """
        path = f"/rest/api/uom/LogicalPartition/{lpar_uuid}"
        xml = await self._post(path, lpar_xml, resource_type="LogicalPartition")
        entries = _parse_feed(xml, path) if xml else []
        return entries[0] if entries else None

    async def delete_logical_partition(self, lpar_uuid: str) -> None:
        """Delete an LPAR. It must be powered off first."""
        await self._delete(f"/rest/api/uom/LogicalPartition/{lpar_uuid}")
