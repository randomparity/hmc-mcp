"""Shared app state and entry points for the hmc-mcp server.

Holds the single :class:`FastMCP` instance (``mcp``) that every domain
tool module registers itself on via ``@mcp.tool``, the read-only /
destructive capability annotations and the frozensets that document them,
and the small sync-run / UUID-resolution / SSH-passthrough helpers used by
the tool bodies.

``server.py`` imports this module and every ``server_*`` domain module; the
domain modules import ``mcp`` back from here (one-way dependency, no
cycles).
"""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .common import client_from_env

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
    "hmc_list_systems",
    "hmc_get_system",
    "hmc_list_lpars",
    "hmc_get_lpar",
    "hmc_find_lpar",
    "hmc_lpar_state",
    "hmc_list_vios",
    "hmc_vios_mappings",
    "hmc_list_resources",
    "hmc_get_job",
    "hmc_list_adapters",
    "hmc_list_volume_groups",
    "hmc_list_virtual_switches",
    "hmc_list_virtual_networks",
    "hmc_list_network_bridges",
    "hmc_list_fc_ports",
    "hmc_list_sea_adapters",
    "hmc_list_partition_templates",
    "hmc_get_partition_template",
    "hmc_list_clusters",
    "hmc_list_shared_storage_pools",
    "hmc_get_shared_storage_pool",
    "hmc_get_pcm_preferences",
    "hmc_get_processed_metric_links",
    "hmc_get_processed_metrics",
    "hmc_get_aggregated_metric_links",
    "hmc_get_aggregated_metrics",
    "hmc_list_users",
    "hmc_get_user",
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


def _run(coro):
    """Run an async client call from a sync tool function."""
    return asyncio.run(coro)


def with_client(fn):
    """Run an async client call against the env-configured HMC.

    Collapses the pervasive ``async def _go`` + ``return _run(_go())`` idiom
    into one line for the common case where the body is a single client call.
    """
    async def _go():
        async with client_from_env() as hmc:
            return await fn(hmc)
    return _run(_go())


# ---------------------------------------------------------------------- #
# UUID -> CLI-name resolution (REST lookup for SSH passthrough tools)
# ---------------------------------------------------------------------- #


async def _system_name(hmc, system_uuid: str) -> str:
    """Resolve a managed-system UUID to its CLI SystemName via REST."""
    entry = await hmc.get_managed_system(system_uuid)
    if not entry or "SystemName" not in entry.get("Resource", {}):
        raise ValueError(
            f"Could not resolve system UUID {system_uuid!r} to a system name. "
            "Use hmc_list_systems to find the system_uuid."
        )
    return entry["Resource"]["SystemName"]


async def _lpar_name(hmc, lpar_uuid: str) -> str:
    """Resolve an LPAR UUID to its CLI PartitionName via REST."""
    entry = await hmc.get_logical_partition(lpar_uuid)
    if not entry or "PartitionName" not in entry.get("Resource", {}):
        raise ValueError(
            f"Could not resolve LPAR UUID {lpar_uuid!r} to a partition name. "
            "Use hmc_list_lpars to find the lpar_uuid."
        )
    return entry["Resource"]["PartitionName"]


def _ssh_with_client(fn, *, system_uuid=None, lpar_uuid=None):
    """Resolve UUIDs to CLI names via REST, then run an SSH tool body.

    Collapses the pervasive ``async def _go`` + ``client_from_env`` +
    ``_system_name``/``_lpar_name`` + ``_run`` scaffold in the SSH-passthrough
    tools, mirroring :func:`with_client` for the REST seam. *fn* is called with
    the resolved system and LPAR CLI names (``None`` when the matching UUID was
    not supplied) and returns an awaitable for the tool result — a
    ``run_hmc_cli(...)`` command, or a call into an :mod:`ssh` helper that runs
    the command itself. The REST session is closed before the SSH command runs.
    """
    async def _go():
        async with client_from_env() as hmc:
            system_name = await _system_name(hmc, system_uuid) if system_uuid else None
            lpar_name = await _lpar_name(hmc, lpar_uuid) if lpar_uuid else None
        return await fn(system_name, lpar_name)

    return _run(_go())



def main_stdio() -> None:
    mcp.run()


def main_http(host: str = "127.0.0.1", port: int = 8000) -> None:
    mcp.run(transport="streamable-http", host=host, port=port)
