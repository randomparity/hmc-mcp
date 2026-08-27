"""HMCClient systems mixin.

The full client is assembled in :mod:`hmc_mcp.client` by inheriting every
domain mixin; this module only defines methods for systems.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .client_parse import _parse_feed
from .client_resolution import (
    PARENT_DISCOVERY_TIMEOUT_SECONDS,
    ambiguity_candidate_ids,
    bounded_parent_systems,
)
from ..errors import HMCError
from ..jobs import (
    power_off_system_job,
    power_off_vios_job,
    power_on_system_job,
    power_on_vios_job,
)


class SystemsMixin:
    list_uom: Callable[..., Awaitable[list[dict[str, Any]]]]
    get_uom: Callable[..., Awaitable[dict[str, Any] | None]]
    search_uom: Callable[..., Awaitable[list[dict[str, Any]]]]
    _get: Callable[..., Awaitable[str]]
    _post: Callable[..., Awaitable[str]]
    submit_job: Callable[..., Awaitable[dict[str, Any] | None]]

    # -- Convenience wrappers for the common resources ----------------- #
    async def get_console_info(self) -> dict[str, Any] | None:
        """ManagementConsole: HMC version, network info, links to systems."""
        # Some HMC firmware builds return HTTP 500 on the unfiltered
        # ManagementConsole feed due to a null SessionId in the response XML.
        # Only that known HTTP 500 means "reachable but unavailable". Auth,
        # transport, parsing, and other HMC errors retain their normal contract.
        try:
            entries = await self.list_uom("ManagementConsole")
            return entries[0] if entries else None
        except HMCError as exc:
            if (
                exc.status_code == 500
                and exc.body is not None
                and "null SessionId" in exc.body
            ):
                return None
            raise

    async def list_managed_systems(self) -> list[dict[str, Any]]:
        # Some HMC firmware builds return HTTP 500 on the unfiltered
        # ManagedSystem feed due to null property values in hardware-inventory
        # sub-elements (e.g. VirtualPersistentMemoryVolume/Uuid,
        # PersistentMemoryDevice/DynamicReconfigurationConnectorIndex, …).
        # The HMC serialiser trips on null-valued sub-fields it cannot encode.
        # Translate that known response into an actionable error rather than
        # making an unavailable inventory indistinguishable from an empty one.
        try:
            return await self.list_uom("ManagedSystem")
        except HMCError as exc:
            if exc.status_code == 500 and "Nested path contains null property" in str(
                exc
            ):
                raise HMCError(
                    "Managed-system inventory is unavailable because this HMC "
                    "firmware could not serialize a null hardware property; "
                    "update the HMC firmware or query a managed system directly",
                    status_code=500,
                    body=exc.body,
                ) from exc
            raise

    async def get_managed_system(self, uuid: str) -> dict[str, Any] | None:
        return await self.get_uom("ManagedSystem", uuid)

    async def find_system_by_name(self, name: str) -> dict[str, Any] | None:
        """Find a managed system by its SystemName (exact match)."""
        results = await self.search_uom("ManagedSystem", "SystemName", name)
        if len(results) > 1:
            ambiguity_candidate_ids(results, "managed-system", name)
            details = ", ".join(
                f"{(entry.get('Resource') or {}).get('SystemName')!r} ({entry.get('UUID')})"
                for entry in sorted(results, key=lambda item: str(item.get("UUID")))
            )
            raise ValueError(f"Ambiguous managed-system name {name!r}: {details}")
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
            f"/rest/api/uom/ManagedSystem/{system_uuid}/do/PowerOn",
            power_on_system_job(),
        )

    async def power_off_system(
        self, system_uuid: str, immediate: bool = False
    ) -> dict[str, Any] | None:
        """Power off a managed system (PowerOff job; immediate skips graceful shutdown)."""

        return await self.submit_job(
            f"/rest/api/uom/ManagedSystem/{system_uuid}/do/PowerOff",
            power_off_system_job(immediate),
        )

    async def find_vios_by_name(
        self, name: str, system_uuid: str | None = None
    ) -> dict[str, Any] | None:
        """Find a Virtual I/O Server by its PartitionName (exact match)."""
        if system_uuid:
            entries = await self.list_vios(system_uuid)
            results = [
                entry
                for entry in entries
                if (entry.get("Resource") or {}).get("PartitionName") == name
            ]
            if len(results) > 1:
                ambiguity_candidate_ids(results, "VIOS", name)
                system = await self.get_managed_system(system_uuid)
                system_name = (system or {}).get("Resource", {}).get("SystemName")
                if not isinstance(system_name, str) or not system_name:
                    raise ValueError(
                        f"Cannot resolve ambiguous VIOS name {name!r}: cannot "
                        f"identify managed system {system_uuid}"
                    )
                details = ", ".join(
                    f"{entry.get('UUID')} on {system_name!r} ({system_uuid})"
                    for entry in sorted(results, key=lambda item: str(item.get("UUID")))
                )
                raise ValueError(f"Ambiguous VIOS name {name!r}: {details}")
            return results[0] if results else None

        results = await self.search_uom("VirtualIOServer", "PartitionName", name)
        if len(results) <= 1:
            return results[0] if results else None

        candidate_ids = ambiguity_candidate_ids(results, "VIOS", name)
        parents: dict[str, list[tuple[str, str]]] = {uuid: [] for uuid in candidate_ids}
        systems = bounded_parent_systems(
            await self.list_managed_systems(), "VIOS", name
        )
        try:
            async with asyncio.timeout(PARENT_DISCOVERY_TIMEOUT_SECONDS):
                for system in systems:
                    parent_uuid = system.get("UUID")
                    parent_name = (system.get("Resource") or {}).get("SystemName")
                    if (
                        not isinstance(parent_uuid, str)
                        or not parent_uuid
                        or not isinstance(parent_name, str)
                        or not parent_name
                    ):
                        raise ValueError(
                            f"Cannot resolve ambiguous VIOS name {name!r}: cannot "
                            "identify managed system from incomplete inventory metadata"
                        )
                    for entry in await self.list_vios(parent_uuid):
                        entry_uuid = str(entry.get("UUID"))
                        if entry_uuid in parents:
                            parents[entry_uuid].append((parent_name, parent_uuid))
        except TimeoutError as exc:
            raise ValueError(
                f"Cannot resolve ambiguous VIOS name {name!r}: parent discovery "
                "timed out; supply managed-system scope"
            ) from exc
        invalid = sorted(uuid for uuid, matches in parents.items() if len(matches) != 1)
        if invalid:
            raise ValueError(
                "Cannot resolve ambiguous VIOS name "
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
        raise ValueError(f"Ambiguous VIOS name {name!r}: {details}")

    async def power_on_vios(self, vios_uuid: str) -> dict[str, Any] | None:
        """Power on a VIOS (PowerOn job)."""

        return await self.submit_job(
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}/do/PowerOn", power_on_vios_job()
        )

    async def power_off_vios(
        self, vios_uuid: str, immediate: bool = False
    ) -> dict[str, Any] | None:
        """Power off a VIOS (PowerOff job; immediate skips graceful shutdown)."""

        return await self.submit_job(
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}/do/PowerOff",
            power_off_vios_job(immediate),
        )

    async def list_vios(self, system_uuid: str | None = None) -> list[dict[str, Any]]:
        if system_uuid:
            path = f"/rest/api/uom/ManagedSystem/{system_uuid}/VirtualIOServer"
            xml = await self._get(path, "VirtualIOServer")
            return _parse_feed(xml, path) if xml else []
        return await self.list_uom("VirtualIOServer")

    async def get_vios_storage_detail(self, vios_uuid: str) -> dict[str, Any] | None:
        """GET VirtualIOServer device mappings.

        Requests the documented ViosSCSIMapping and ViosFCMapping groups and
        returns the parsed entry with both mapping collections populated.
        """
        path = (
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}"
            "?group=ViosSCSIMapping&group=ViosFCMapping"
        )
        xml = await self._get(path, "VirtualIOServer")
        if not xml:
            return None
        entries = _parse_feed(xml, path)
        return entries[0] if entries else None
