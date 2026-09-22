"""HMCClient systems mixin.

The full client is assembled in :mod:`hmc_mcp.client` by inheriting every
domain mixin; this module only defines methods for systems.
"""

from __future__ import annotations

import logging
from typing import Any

from ..errors import HMCError
from ..jobs import (
    power_off_system_job,
    power_off_vios_job,
    power_on_system_job,
    power_on_vios_job,
)
from .client_contracts import SystemsClient, _reject_non_uuid_path_argument
from .client_parse import _parse_feed
from .client_resolution import (
    ambiguity_candidate_ids,
    ambiguous_parent_details,
)

_logger = logging.getLogger(__name__)


class SystemsMixin:
    # -- Convenience wrappers for the common resources ----------------- #
    async def get_console_info(self: SystemsClient) -> dict[str, Any] | None:
        """ManagementConsole: HMC version, network info, links to systems."""
        # Some HMC firmware builds return HTTP 500 on the unfiltered
        # ManagementConsole feed over a null nested property (observed:
        # Session/SessionId/Value). Translate that known response into an
        # actionable error rather than making a firmware failure
        # indistinguishable from an empty feed. The marker is the
        # live-confirmed text the two managed-system guards below also match
        # (ADR 0138), but it is tested against the body rather than their
        # str(exc): the rendered detail carries only the first
        # Message/msg/error element of an XML body, or its first 500
        # characters when the body is not XML (errors.py), while the body is
        # what the transport received. No raw body for this endpoint has
        # been captured, so the wider surface is the one that holds whatever
        # shape a live capture turns out to show. The message names no
        # particular property because the marker does not establish one; the
        # HMC's own detail still reaches the operator through the body.
        try:
            entries = await self.list_uom("ManagementConsole")
            return entries[0] if entries else None
        except HMCError as exc:
            if exc.status_code == 500 and (
                "Nested path contains null property" in (exc.body or "")
            ):
                raise HMCError(
                    "Management-console inventory is unavailable because this HMC "
                    "firmware could not serialize a null property; update the HMC "
                    "firmware and retry",
                    status_code=500,
                    body=exc.body,
                ) from exc
            raise

    async def _quick_all_system_names(self: SystemsClient) -> dict[str, str]:
        """UUID -> SystemName map from GET .../ManagedSystem/quick/All.

        Not documented in this repo's vendored HMC REST API reference (only
        the per-UUID quick/{Property} form is); evidenced by IBM's public
        project-pim repository (ADR 0138). No typed Accept header, matching
        get_quick_property's precedent (core.py) that a uom+xml header 406s
        on quick/ endpoints, and project-pim's own quick/All calls, which
        send none either. Used only as a fallback when the direct/unfiltered
        feed trips the null-property serialization bug, so an unexpected
        shape here is treated defensively: entries missing UUID or
        SystemName are skipped rather than raised.
        """
        resp = await self._request(
            "GET",
            "/rest/api/uom/ManagedSystem/quick/All",
            headers={"Accept": "*/*"},
        )
        if resp.status_code != 200:
            raise HMCError(
                "GET /rest/api/uom/ManagedSystem/quick/All failed",
                resp.status_code,
                resp.text,
            )
        try:
            summaries = resp.json()
        except ValueError as exc:
            raise HMCError(
                "GET /rest/api/uom/ManagedSystem/quick/All returned invalid "
                f"JSON: {str(exc)[:500]}"
            ) from exc
        except RecursionError as exc:
            # json.loads recurses per nesting level; RecursionError carries
            # no message, hence the fixed clause.
            raise HMCError(
                "GET /rest/api/uom/ManagedSystem/quick/All returned invalid "
                "JSON: document nesting is too deep"
            ) from exc
        if not isinstance(summaries, list):
            raise HMCError(
                "GET /rest/api/uom/ManagedSystem/quick/All returned a JSON "
                f"{type(summaries).__name__}; expected an array"
            )
        return {
            entry["UUID"]: entry["SystemName"]
            for entry in summaries
            if isinstance(entry, dict) and "UUID" in entry and "SystemName" in entry
        }

    async def list_managed_systems(self: SystemsClient) -> list[dict[str, Any]]:
        # Some firmware 500s on the unfiltered feed over a null
        # hardware-inventory property (e.g. VirtualPersistentMemoryVolume/Uuid).
        # quick/All + find_system_by_name (a different, working path) resolve
        # what they can; a system that still fails (or resolves ambiguously)
        # is skipped with a warning rather than failing the whole call.
        try:
            return await self.list_uom("ManagedSystem")
        except HMCError as exc:
            if not (
                exc.status_code == 500
                and "Nested path contains null property" in str(exc)
            ):
                raise
            quick_all_exc: HMCError | None = None
            try:
                names = await self._quick_all_system_names()
            except HMCError as qa_exc:
                names = {}
                quick_all_exc = qa_exc
            resolved: list[dict[str, Any]] = []
            for name in names.values():
                try:
                    entry = await self.find_system_by_name(name)
                except (HMCError, ValueError) as resolution_exc:
                    _logger.warning(
                        "Skipping managed system %r during inventory fallback: %s",
                        name,
                        resolution_exc,
                    )
                    continue
                if entry is not None:
                    resolved.append(entry)
                else:
                    _logger.warning(
                        "Skipping managed system %r during inventory fallback: not found",
                        name,
                    )
            if resolved:
                return resolved
            raise HMCError(
                "Managed-system inventory is unavailable because this HMC "
                "firmware could not serialize a null hardware property; "
                "update the HMC firmware or query a managed system directly",
                status_code=500,
                body=exc.body,
            ) from (quick_all_exc or exc)

    async def get_managed_system(
        self: SystemsClient, uuid: str
    ) -> dict[str, Any] | None:
        # Some firmware 500s on a direct UUID fetch over a null hardware
        # property (see list_managed_systems); quick/All supplies the
        # missing name so find_system_by_name (a different, working path)
        # can resolve it.
        try:
            return await self.get_uom("ManagedSystem", uuid)
        except HMCError as exc:
            if not (
                exc.status_code == 500
                and "Nested path contains null property" in str(exc)
            ):
                raise
            entry = None
            fallback_exc: Exception | None = None
            try:
                names = await self._quick_all_system_names()
                name = names.get(uuid)
                if name:
                    entry = await self.find_system_by_name(name)
            except (HMCError, ValueError) as fb_exc:
                fallback_exc = fb_exc
            if entry is None:
                raise HMCError(
                    f"Managed system {uuid} is unavailable because this "
                    "HMC firmware could not serialize a null hardware "
                    "property, and it could not be resolved from the "
                    "managed-system summary; update the HMC firmware or "
                    "query the system by name with systems show",
                    status_code=500,
                    body=exc.body,
                ) from (fallback_exc or exc)
            return entry

    async def find_system_by_name(
        self: SystemsClient, name: str
    ) -> dict[str, Any] | None:
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
        self: SystemsClient, system_uuid: str, system_xml: str
    ) -> dict[str, Any] | None:
        """Modify a managed system's properties (POST a partial ManagedSystem doc).

        Supported fields include system name, power-off policy, LPAR start
        policy, pending memory region size, huge pages, and mirroring mode.
        See documents.build_managed_system_document for the document builder.
        """
        _reject_non_uuid_path_argument("system_uuid", system_uuid)
        path = f"/rest/api/uom/ManagedSystem/{system_uuid}"
        xml = await self._post(path, system_xml, resource_type="ManagedSystem")
        entries = _parse_feed(xml, path) if xml else []
        return entries[0] if entries else None

    # Managed-system / VIOS power jobs
    async def power_on_system(
        self: SystemsClient, system_uuid: str
    ) -> dict[str, Any] | None:
        """Power on a managed system (PowerOn job)."""

        _reject_non_uuid_path_argument("system_uuid", system_uuid)
        return await self.submit_job(
            f"/rest/api/uom/ManagedSystem/{system_uuid}/do/PowerOn",
            power_on_system_job(),
        )

    async def power_off_system(
        self: SystemsClient, system_uuid: str, immediate: bool = False
    ) -> dict[str, Any] | None:
        """Power off a managed system (PowerOff job; immediate skips graceful shutdown)."""

        _reject_non_uuid_path_argument("system_uuid", system_uuid)
        return await self.submit_job(
            f"/rest/api/uom/ManagedSystem/{system_uuid}/do/PowerOff",
            power_off_system_job(immediate),
        )

    async def find_vios_by_name(
        self: SystemsClient, name: str, system_uuid: str | None = None
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

        details = await ambiguous_parent_details(
            results,
            await self.list_managed_systems(),
            "VIOS",
            name,
            self.list_vios,
        )
        raise ValueError(f"Ambiguous VIOS name {name!r}: {details}")

    async def power_on_vios(
        self: SystemsClient, vios_uuid: str
    ) -> dict[str, Any] | None:
        """Power on a VIOS (PowerOn job)."""

        _reject_non_uuid_path_argument("vios_uuid", vios_uuid)
        return await self.submit_job(
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}/do/PowerOn", power_on_vios_job()
        )

    async def power_off_vios(
        self: SystemsClient, vios_uuid: str, immediate: bool = False
    ) -> dict[str, Any] | None:
        """Power off a VIOS (PowerOff job; immediate skips graceful shutdown)."""

        _reject_non_uuid_path_argument("vios_uuid", vios_uuid)
        return await self.submit_job(
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}/do/PowerOff",
            power_off_vios_job(immediate),
        )

    async def list_vios(
        self: SystemsClient, system_uuid: str | None = None
    ) -> list[dict[str, Any]]:
        if system_uuid:
            _reject_non_uuid_path_argument("system_uuid", system_uuid)
            path = f"/rest/api/uom/ManagedSystem/{system_uuid}/VirtualIOServer"
            xml = await self._get(path, "VirtualIOServer")
            return _parse_feed(xml, path) if xml else []
        return await self.list_uom("VirtualIOServer")

    async def get_vios_storage_detail(
        self: SystemsClient, vios_uuid: str
    ) -> dict[str, Any] | None:
        """GET VirtualIOServer device mappings.

        Requests the documented ViosSCSIMapping and ViosFCMapping groups and
        returns the parsed entry with both mapping collections populated.
        """
        _reject_non_uuid_path_argument("vios_uuid", vios_uuid)
        path = (
            f"/rest/api/uom/VirtualIOServer/{vios_uuid}"
            "?group=ViosSCSIMapping&group=ViosFCMapping"
        )
        xml = await self._get(path, "VirtualIOServer")
        if not xml:
            return None
        entries = _parse_feed(xml, path)
        return entries[0] if entries else None
