"""Resolve managed-system, partition, and VIOS names and UUIDs."""

from __future__ import annotations

import re

from .client import HMCClient

# Canonical UUID shape: 8-4-4-4-12 hex groups. Any 36-char dash-containing
# string is NOT a UUID (system/partition names can collide with that shape), so
# the predicate must reject non-hex characters or name/uuid disambiguation
# silently misroutes them as UUIDs.
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def is_uuid(value: str) -> bool:
    """True if *value* is a canonical 8-4-4-4-12 hex UUID."""
    return _UUID_RE.fullmatch(value) is not None


async def resolve_system_uuid(hmc: HMCClient, value: str) -> str:
    """Resolve a managed-system name or pass through its UUID."""
    if is_uuid(value):
        return value
    entry = await hmc.find_system_by_name(value)
    if not entry or not entry.get("UUID"):
        raise ValueError(
            f"No managed system named {value!r} found. "
            "Use hmc_list_systems to list available systems."
        )
    return str(entry["UUID"])


async def resolve_system_name(hmc: HMCClient, value: str) -> str:
    """Pass through a system name or resolve a UUID to its SystemName."""
    if not is_uuid(value):
        return value
    entry = await hmc.get_managed_system(value)
    name = ((entry or {}).get("Resource") or {}).get("SystemName")
    if not name:
        raise ValueError(
            f"Managed system {value!r} has no SystemName. "
            "Use hmc_list_systems to inspect available systems."
        )
    return str(name)


async def resolve_lpar_uuid(
    hmc: HMCClient, value: str, *, system_name_or_uuid: str | None = None
) -> str:
    """Resolve an LPAR name or pass through its UUID."""
    if is_uuid(value):
        return value
    system_uuid = (
        await resolve_system_uuid(hmc, system_name_or_uuid)
        if system_name_or_uuid is not None
        else None
    )
    entry = (
        await hmc.find_partition_by_name(value, system_uuid=system_uuid)
        if system_uuid is not None
        else await hmc.find_partition_by_name(value)
    )
    if not entry or not entry.get("UUID"):
        raise ValueError(
            f"No LPAR named {value!r} found. "
            "Use hmc_list_lpars to list available partitions."
        )
    return str(entry["UUID"])


async def resolve_vios_uuid(
    hmc: HMCClient, value: str, *, system_name_or_uuid: str | None = None
) -> str:
    """Resolve a VIOS name or pass through its UUID."""
    if is_uuid(value):
        return value
    system_uuid = (
        await resolve_system_uuid(hmc, system_name_or_uuid)
        if system_name_or_uuid is not None
        else None
    )
    entry = (
        await hmc.find_vios_by_name(value, system_uuid=system_uuid)
        if system_uuid is not None
        else await hmc.find_vios_by_name(value)
    )
    if not entry or not entry.get("UUID"):
        raise ValueError(
            f"No VIOS named {value!r} found. "
            "Use hmc_list_vios to list available Virtual I/O Servers."
        )
    return str(entry["UUID"])
