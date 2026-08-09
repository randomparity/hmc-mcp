"""Shared app state and entry points for the hmc-mcp server.

Holds the single :class:`FastMCP` instance (``mcp``) that every domain
tool module registers itself on via ``@mcp.tool``, the read-only /
destructive capability annotations and the frozensets that document them,
and the small sync-run / name-or-UUID-resolution (REST-first, SSH-fallback)
/ SSH-passthrough helpers used by the tool bodies.

``server.py`` imports this module and every ``server_*`` domain module; the
domain modules import ``mcp`` back from here (one-way dependency, no
cycles).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .common import client_from_env, is_uuid, run_with_client
from .config import HMCConfig
from .ssh import _ssh_lpar_name, _ssh_system_name

mcp = FastMCP(
    name="hmc-mcp",
    instructions=(
        "Tools for querying and operating IBM Power systems via the HMC "
        "(Hardware Management Console) REST API. Managed systems are Power "
        "servers; logical partitions (LPARs) are the AIX/Linux/IBM i virtual "
        "servers running on them. Most read tools return parsed uom Atom "
        "entries as JSON."
    ),
)

# Tool capability annotations. MCP clients and gateways use these to separate
# read-only, state-changing, and destructive tools (e.g. to auto-approve the
# first, warn before the last). Tools are tagged inline on their @mcp.tool
# decorator with _READ_ONLY or _DESTRUCTIVE; everything else is state-changing
# and intentionally untagged. tests/test_capabilities.py asserts the live tool
# registry matches the documented READ_ONLY_TOOLS / DESTRUCTIVE_TOOLS sets, so
# a new tool must be placed in exactly one category there and tagged here.
_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_DESTRUCTIVE = ToolAnnotations(destructiveHint=True)

READ_ONLY_TOOLS = frozenset({
    "hmc_console_info",
    "hmc_systems",
    "hmc_lpars",
    "hmc_lpar_summary",
    "hmc_vios",
    "hmc_list_resources",
    "hmc_get_job",
    "hmc_recent_jobs",
    "hmc_capacity_report",
    "hmc_find_placement",
    "hmc_find_system",
    "hmc_wait_for_job",
    "hmc_list_adapters",
    "hmc_list_volume_groups",
    "hmc_list_virtual_switches",
    "hmc_list_virtual_networks",
    "hmc_list_network_bridges",
    "hmc_list_fc_ports",
    "hmc_list_sea_adapters",
    "hmc_partition_templates",
    "hmc_list_clusters",
    "hmc_shared_storage_pools",
    "hmc_get_pcm_preferences",
    "hmc_processed_metrics",
    "hmc_aggregated_metrics",
    "hmc_users",
    "hmc_list_password_policies",
    "hmc_get_ldap_config",
    "hmc_get_available_hmc_ptfs",
    "hmc_list_vios_backups",
    "hmc_get_lpar_description",
    "hmc_get_lpar_msp",
    "hmc_get_proc_compat_modes",
    "hmc_get_lpar_proc_compat",
    "hmc_list_io_slots",
    "hmc_list_memory_pools",
    "hmc_list_vnics",
})

DESTRUCTIVE_TOOLS = frozenset({
    "hmc_power_off_lpar",
    "hmc_delete_lpar",
    "hmc_delete_vios",
    "hmc_delete_adapter",
    "hmc_delete_virtual_network",
    "hmc_delete_media_repository",
    "hmc_delete_logical_unit",
    "hmc_delete_user",
    "hmc_delete_password_policy",
    "hmc_remove_ldap_config",
    "hmc_remove_memory_pool",
    "hmc_remove_vnic",
    "hmc_power_off_system",
    "hmc_power_off_vios",
    "hmc_migrate_abort_lpar",
    "hmc_remote_restart_lpar",
    "hmc_restore_vios",
    "hmc_restore_lpar_profiles",
    "hmc_sync_lpar_profile",
})


def _run(fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run a coroutine-returning closure from a sync tool function."""
    return asyncio.run(fn())


def with_client(fn):
    """Run an async client call against the env-configured HMC.

    Collapses the pervasive ``async def _go`` + ``return _run(_go)`` idiom
    into one line for the common case where the body is a single client call.
    """
    return run_with_client(client_from_env, fn)


# ---------------------------------------------------------------------- #
# Name-or-UUID resolution (REST-first, SSH fallback) for SSH tools
# ---------------------------------------------------------------------- #


async def _system_name_from_rest(hmc, system_uuid: str) -> str:
    """Resolve a managed-system UUID to its CLI SystemName via REST."""
    entry = await hmc.get_managed_system(system_uuid)
    if not entry or "SystemName" not in entry.get("Resource", {}):
        raise ValueError(
            f"Could not resolve system UUID {system_uuid!r} to a system name. "
            "Use hmc_systems to find the system_uuid."
        )
    return entry["Resource"]["SystemName"]


async def _lpar_name_from_rest(hmc, lpar_uuid: str) -> str:
    """Resolve an LPAR UUID to its CLI PartitionName via REST."""
    entry = await hmc.get_logical_partition(lpar_uuid)
    if not entry or "PartitionName" not in entry.get("Resource", {}):
        raise ValueError(
            f"Could not resolve LPAR UUID {lpar_uuid!r} to a partition name. "
            "Use hmc_lpars to find the lpar_uuid."
        )
    return entry["Resource"]["PartitionName"]


async def _resolve_system_name(
    config: HMCConfig, system_name_or_uuid: str | None
) -> str | None:
    """Resolve a system name-or-uuid to a CLI SystemName.

    Names pass through untouched (no REST needed). UUIDs are resolved via
    REST, falling back to an ``lssyscfg`` name lookup over SSH only when the
    REST transport is unreachable (``httpx.HTTPError``). A REST 4xx/5xx
    (``HMCError``) is *not* a transport failure — REST answered and the UUID
    is unknown, so the error surfaces rather than falling back.
    """
    if system_name_or_uuid is None or not is_uuid(system_name_or_uuid):
        return system_name_or_uuid
    try:
        async with client_from_env() as hmc:
            return await _system_name_from_rest(hmc, system_name_or_uuid)
    except httpx.HTTPError:
        return await _ssh_system_name(config, system_name_or_uuid)


async def _resolve_lpar_name(
    config: HMCConfig,
    lpar_name_or_uuid: str | None,
    system_name: str | None = None,
) -> str | None:
    """Resolve an LPAR name-or-uuid to a CLI PartitionName.

    Same REST-first / SSH-fallback contract as :func:`_resolve_system_name`;
    *system_name* (when known) scopes the SSH name lookup to one system.
    """
    if lpar_name_or_uuid is None or not is_uuid(lpar_name_or_uuid):
        return lpar_name_or_uuid
    try:
        async with client_from_env() as hmc:
            return await _lpar_name_from_rest(hmc, lpar_name_or_uuid)
    except httpx.HTTPError:
        return await _ssh_lpar_name(config, lpar_name_or_uuid, system_name)


# ---------------------------------------------------------------------- #
# UUID resolvers for REST tools
# ---------------------------------------------------------------------- #


async def _resolve_system_uuid(hmc, system_name_or_uuid: str) -> str:
    """Resolve a system name-or-UUID to a UUID for REST calls.

    If the value is already a UUID it is returned as-is. Otherwise it is
    treated as a SystemName; :meth:`find_system_by_name` is called and its
    UUID is returned. Raises ``ValueError`` when the name cannot be found.
    """
    if is_uuid(system_name_or_uuid):
        return system_name_or_uuid
    entry = await hmc.find_system_by_name(system_name_or_uuid)
    if not entry or not entry.get("UUID"):
        raise ValueError(
            f"No managed system named {system_name_or_uuid!r} found. "
            "Use hmc_systems to list available systems."
        )
    return str(entry["UUID"])


async def _resolve_lpar_uuid(hmc, lpar_name_or_uuid: str) -> str:
    """Resolve an LPAR name-or-UUID to a UUID for REST calls.

    If the value is already a UUID it is returned as-is. Otherwise it is
    treated as a PartitionName; :meth:`find_partition_by_name` is called and
    its UUID is returned. Raises ``ValueError`` when the name cannot be found.
    """
    if is_uuid(lpar_name_or_uuid):
        return lpar_name_or_uuid
    entry = await hmc.find_partition_by_name(lpar_name_or_uuid)
    if not entry or not entry.get("UUID"):
        raise ValueError(
            f"No LPAR named {lpar_name_or_uuid!r} found. "
            "Use hmc_lpars to list available partitions."
        )
    return str(entry["UUID"])


async def _resolve_vios_uuid(hmc, vios_name_or_uuid: str) -> str:
    """Resolve a VIOS name-or-UUID to a UUID for REST calls.

    If the value is already a UUID it is returned as-is. Otherwise it is
    treated as a PartitionName; :meth:`find_vios_by_name` is called and its
    UUID is returned. Raises ``ValueError`` when the name cannot be found.
    """
    if is_uuid(vios_name_or_uuid):
        return vios_name_or_uuid
    entry = await hmc.find_vios_by_name(vios_name_or_uuid)
    if not entry or not entry.get("UUID"):
        raise ValueError(
            f"No VIOS named {vios_name_or_uuid!r} found. "
            "Use hmc_vios to list available Virtual I/O Servers."
        )
    return str(entry["UUID"])


def _ssh_with_client(fn, *, system_name_or_uuid=None, lpar_name_or_uuid=None):
    """Resolve name-or-uuid args to CLI names, then run an SSH tool body.

    Collapses the pervasive ``async def _go`` + name resolution + ``_run``
    scaffold in the SSH-passthrough tools, mirroring :func:`with_client` for
    the SSH seam. *system_name_or_uuid* and *lpar_name_or_uuid* may each be a
    CLI name (passed through untouched) or a UUID (resolved via REST, falling
    back to an ``lssyscfg`` name lookup over SSH when the REST transport is
    unreachable). *fn* is called with the env-configured ``HMCConfig`` followed
    by the resolved system and LPAR CLI names (``None`` when the matching arg
    was not supplied) and returns an awaitable for the tool result — a
    ``run_hmc_command(...)`` call, or a call into an :mod:`ssh` helper that
    runs the command itself.
    """
    async def _go():
        config = HMCConfig()
        system_name = await _resolve_system_name(config, system_name_or_uuid)
        lpar_name = await _resolve_lpar_name(config, lpar_name_or_uuid, system_name)
        return await fn(config, system_name, lpar_name)

    return _run(_go)



def main_stdio() -> None:
    mcp.run()


def main_http(host: str = "127.0.0.1", port: int = 8000) -> None:
    mcp.run(transport="streamable-http", host=host, port=port)
