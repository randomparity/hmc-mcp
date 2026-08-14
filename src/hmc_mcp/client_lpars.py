"""HMCClient lpars mixin.

The full client is assembled in :mod:`hmc_mcp.client` by inheriting every
domain mixin; this module only defines methods for lpars.
"""

from __future__ import annotations

from typing import Any

from .client_contracts import LparsClient
from .client_parse import _parse_feed
from .client_resolution import ambiguity_candidate_ids, bounded_parent_systems


class LparsMixin:
    async def list_logical_partitions(
        self: LparsClient, system_uuid: str | None = None
    ) -> list[dict[str, Any]]:
        if system_uuid:
            path = f"/rest/api/uom/ManagedSystem/{system_uuid}/LogicalPartition"
            xml = await self._get(path, "LogicalPartition")
            return _parse_feed(xml, path) if xml else []
        return await self.list_uom("LogicalPartition")

    async def get_logical_partition(
        self: LparsClient, uuid: str
    ) -> dict[str, Any] | None:
        return await self.get_uom("LogicalPartition", uuid)

    async def find_partition_by_name(
        self: LparsClient, name: str, system_uuid: str | None = None
    ) -> dict[str, Any] | None:
        if system_uuid:
            entries = await self.list_logical_partitions(system_uuid)
            results = [
                entry
                for entry in entries
                if (entry.get("Resource") or {}).get("PartitionName") == name
            ]
            if len(results) > 1:
                ambiguity_candidate_ids(results, "LPAR", name)
                system = await self.get_managed_system(system_uuid)
                system_name = (system or {}).get("Resource", {}).get("SystemName")
                if not isinstance(system_name, str) or not system_name:
                    raise ValueError(
                        f"Cannot resolve ambiguous LPAR name {name!r}: cannot "
                        f"identify managed system {system_uuid}"
                    )
                details = ", ".join(
                    f"{entry.get('UUID')} on {system_name!r} ({system_uuid})"
                    for entry in sorted(results, key=lambda item: str(item.get("UUID")))
                )
                raise ValueError(f"Ambiguous LPAR name {name!r}: {details}")
            return results[0] if results else None

        results = await self.search_uom("LogicalPartition", "PartitionName", name)
        if len(results) <= 1:
            return results[0] if results else None

        candidate_ids = ambiguity_candidate_ids(results, "LPAR", name)
        parents: dict[str, list[tuple[str, str]]] = {uuid: [] for uuid in candidate_ids}
        systems = bounded_parent_systems(
            await self.list_managed_systems(), "LPAR", name
        )
        for system in systems:
            system_uuid = system.get("UUID")
            system_name = (system.get("Resource") or {}).get("SystemName")
            if (
                not isinstance(system_uuid, str)
                or not system_uuid
                or not isinstance(system_name, str)
                or not system_name
            ):
                raise ValueError(
                    f"Cannot resolve ambiguous LPAR name {name!r}: cannot identify "
                    "managed system from incomplete inventory metadata"
                )
            for entry in await self.list_logical_partitions(system_uuid):
                entry_uuid = str(entry.get("UUID"))
                if entry_uuid in parents:
                    parents[entry_uuid].append((system_name, system_uuid))
        invalid = sorted(uuid for uuid, matches in parents.items() if len(matches) != 1)
        if invalid:
            raise ValueError(
                "Cannot resolve ambiguous LPAR name "
                f"{name!r}: candidates {', '.join(invalid)} must each belong to "
                "exactly one managed system"
            )
        details = ", ".join(
            f"{uuid} on {parents[uuid][0][0]!r} ({parents[uuid][0][1]})"
            for uuid in sorted(
                candidate_ids,
                key=lambda value: (parents[value][0][0], parents[value][0][1], value),
            )
        )
        raise ValueError(f"Ambiguous LPAR name {name!r}: {details}")

    async def create_logical_partition(
        self: LparsClient, system_uuid: str, lpar_xml: str
    ) -> dict[str, Any] | None:
        """Create an LPAR on a managed system.

        PUTs a LogicalPartition document (see documents.build_lpar_document)
        to /rest/api/uom/ManagedSystem/{system_uuid}/LogicalPartition and
        returns the created partition entry.

        Omits X-HMC-Schema-Version header — some HMC firmware versions return
        HTTP 406 for this PUT when the schema-version header is present.
        """
        path = f"/rest/api/uom/ManagedSystem/{system_uuid}/LogicalPartition"
        xml = await self._put(
            path,
            lpar_xml,
            resource_type="LogicalPartition",
            include_schema_version=False,
        )
        entries = _parse_feed(xml, path) if xml else []
        return entries[0] if entries else None

    async def modify_logical_partition(
        self: LparsClient, lpar_uuid: str, lpar_xml: str
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

    async def delete_logical_partition(self: LparsClient, lpar_uuid: str) -> None:
        """Delete an LPAR. It must be powered off first."""
        await self._delete(f"/rest/api/uom/LogicalPartition/{lpar_uuid}")
