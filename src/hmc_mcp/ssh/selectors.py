"""Resolve public resource selectors for HMC SSH commands."""

from __future__ import annotations

from typing import overload

from ..client import HMCClient
from ..resource_identity import is_uuid
from ..config import HMCConfig
from ..errors import HMCTransportError
from .lpar import _ssh_lpar_name, _ssh_system_name


async def _system_name_from_rest(hmc: HMCClient, system_uuid: str) -> str:
    entry = await hmc.get_managed_system(system_uuid)
    if not entry or "SystemName" not in entry.get("Resource", {}):
        raise ValueError(
            f"Could not resolve system UUID {system_uuid!r} to a system name. "
            "List managed systems to find the system UUID."
        )
    return entry["Resource"]["SystemName"]


async def _lpar_name_from_rest(hmc: HMCClient, lpar_uuid: str) -> str:
    entry = await hmc.get_logical_partition(lpar_uuid)
    if not entry or "PartitionName" not in entry.get("Resource", {}):
        raise ValueError(
            f"Could not resolve LPAR UUID {lpar_uuid!r} to a partition name. "
            "List logical partitions to find the partition UUID."
        )
    return entry["Resource"]["PartitionName"]


async def resolve_system_name(
    config: HMCConfig,
    system_name_or_uuid: str | None,
) -> str | None:
    """Resolve a system UUID through REST, with SSH transport fallback."""
    if system_name_or_uuid is None or not is_uuid(system_name_or_uuid):
        return system_name_or_uuid
    try:
        async with HMCClient(config) as hmc:
            return await _system_name_from_rest(hmc, system_name_or_uuid)
    except HMCTransportError:
        return await _ssh_system_name(config, system_name_or_uuid)


async def resolve_lpar_name(
    config: HMCConfig,
    lpar_name_or_uuid: str | None,
    system_name: str | None = None,
) -> str | None:
    """Resolve an LPAR UUID through REST, with SSH transport fallback."""
    if lpar_name_or_uuid is None or not is_uuid(lpar_name_or_uuid):
        return lpar_name_or_uuid
    try:
        async with HMCClient(config) as hmc:
            return await _lpar_name_from_rest(hmc, lpar_name_or_uuid)
    except HMCTransportError:
        return await _ssh_lpar_name(config, lpar_name_or_uuid, system_name)


@overload
async def resolve_ssh_names(
    config: HMCConfig,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
) -> tuple[str, str]: ...


@overload
async def resolve_ssh_names(
    config: HMCConfig,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str | None,
) -> tuple[str, str | None]: ...


@overload
async def resolve_ssh_names(
    config: HMCConfig,
    system_name_or_uuid: str | None,
    lpar_name_or_uuid: str,
) -> tuple[str | None, str]: ...


@overload
async def resolve_ssh_names(
    config: HMCConfig,
    system_name_or_uuid: str | None,
    lpar_name_or_uuid: str | None,
) -> tuple[str | None, str | None]: ...


async def resolve_ssh_names(
    config: HMCConfig,
    system_name_or_uuid: str | None,
    lpar_name_or_uuid: str | None,
) -> tuple[str | None, str | None]:
    system_name = await resolve_system_name(config, system_name_or_uuid)
    lpar_name = await resolve_lpar_name(config, lpar_name_or_uuid, system_name)
    return system_name, lpar_name
