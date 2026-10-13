"""Resolve managed-system, partition, and VIOS names and UUIDs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from hmcpctl.client.client_resolution import summary_for

if TYPE_CHECKING:
    from hmcpctl.client.core import HMCClient

# Canonical UUID shape: 8-4-4-4-12 hex groups. Any 36-char dash-containing
# string is NOT a UUID (system/partition names can collide with that shape), so
# the predicate must reject non-hex characters or name/uuid disambiguation
# silently misroutes them as UUIDs.
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


class ResourceNotFoundError(ValueError):
    """A named resource selector did not resolve to an HMC resource."""

    def __init__(self, resource_kind: str, selector: str, message: str) -> None:
        super().__init__(message)
        self.resource_kind = resource_kind
        self.selector = selector


def is_uuid(value: str) -> bool:
    """True if *value* is a canonical 8-4-4-4-12 hex UUID."""
    return _UUID_RE.fullmatch(value) is not None


def optional_system_selector(value: str | None) -> str | None:
    """Return an optional managed-system selector, reading a blank one as absent.

    ADR 0094: MCP clients serialise an unset optional string as ``""``, so a
    blank or whitespace-only selector means "no system named", never a system
    named ``""``. Surrounding whitespace is dropped from a non-blank one. The
    optional-selector resolvers, reads, and guarded mutations apply this one
    rule, so the same input scopes a read and a mutation the same way.
    """
    return (value or "").strip() or None


async def resolve_system_uuid(hmc: HMCClient, value: str) -> str:
    """Resolve a managed-system name or pass through its UUID.

    The name is matched exactly against ``ManagedSystem/quick/All``: the
    ``SystemName`` search times out for a system with no connection (#1501).
    """
    if is_uuid(value):
        return value
    uuids = [
        summary.get("UUID")
        for summary in await hmc.list_system_summaries()
        if isinstance(summary, Mapping) and summary.get("SystemName") == value
    ]
    if any(not isinstance(uuid, str) or not uuid for uuid in uuids):
        raise ValueError(
            f"Cannot resolve managed-system name {value!r}: its UUID metadata is "
            "incomplete"
        )
    uuids = list(dict.fromkeys(uuids))
    if len(uuids) > 1:
        details = ", ".join(f"{value!r} ({uuid})" for uuid in sorted(uuids))
        raise ValueError(f"Ambiguous managed-system name {value!r}: {details}")
    if not uuids:
        raise ResourceNotFoundError(
            "managed system",
            value,
            f"No managed system named {value!r} found. "
            "Use hmc_list_systems to list available systems.",
        )
    return str(uuids[0])


def _named(name: object) -> str | None:
    return name if isinstance(name, str) and name.strip() else None


def _resource_name(entry: dict[str, Any] | None, field: str) -> str | None:
    resource = entry.get("Resource") if entry else None
    return _named(resource.get(field) if isinstance(resource, Mapping) else None)


async def system_name_from_uuid(hmc: HMCClient, system_uuid: str) -> str | None:
    """Return the managed system's ``SystemName``, or None when it is unusable.

    Read from ``ManagedSystem/quick/All``: ``ManagedSystem/{uuid}`` times out
    for a system with no connection (#1501).
    """
    summaries = await hmc.list_system_summaries()
    return _named(summary_for(summaries, system_uuid).get("SystemName"))


async def lpar_name_from_uuid(hmc: HMCClient, lpar_uuid: str) -> str | None:
    """Return the partition's REST ``PartitionName``, or None when it is unusable."""
    return _resource_name(await hmc.get_logical_partition(lpar_uuid), "PartitionName")


async def resolve_system_name(hmc: HMCClient, value: str) -> str:
    """Pass through a system name or resolve a UUID to its SystemName."""
    if not is_uuid(value):
        return value
    name = await system_name_from_uuid(hmc, value)
    if name is None:
        raise ResourceNotFoundError(
            "managed system",
            value,
            f"Managed system {value!r} has no SystemName. "
            "Use hmc_list_systems to inspect available systems.",
        )
    return name


async def resolve_lpar_uuid(
    hmc: HMCClient, value: str, *, system_name_or_uuid: str | None = None
) -> str:
    """Resolve an LPAR name or pass through its UUID."""
    if is_uuid(value):
        return value
    selector = optional_system_selector(system_name_or_uuid)
    system_uuid = (
        await resolve_system_uuid(hmc, selector) if selector is not None else None
    )
    entry = (
        await hmc.find_partition_by_name(value, system_uuid=system_uuid)
        if system_uuid is not None
        else await hmc.find_partition_by_name(value)
    )
    if not entry or not entry.get("UUID"):
        raise ResourceNotFoundError(
            "LPAR",
            value,
            f"No LPAR named {value!r} found. "
            "Use hmc_list_lpars to list available partitions.",
        )
    return str(entry["UUID"])


async def resolve_vios_uuid(
    hmc: HMCClient, value: str, *, system_name_or_uuid: str | None = None
) -> str:
    """Resolve a VIOS name or pass through its UUID."""
    if is_uuid(value):
        return value
    selector = optional_system_selector(system_name_or_uuid)
    system_uuid = (
        await resolve_system_uuid(hmc, selector) if selector is not None else None
    )
    entry = (
        await hmc.find_vios_by_name(value, system_uuid=system_uuid)
        if system_uuid is not None
        else await hmc.find_vios_by_name(value)
    )
    if not entry or not entry.get("UUID"):
        raise ResourceNotFoundError(
            "VIOS",
            value,
            f"No VIOS named {value!r} found. "
            "Use hmc_list_vios to list available Virtual I/O Servers.",
        )
    return str(entry["UUID"])
