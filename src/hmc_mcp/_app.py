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
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Literal, TypeVar, overload

import httpx
from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .client import HMCClient
from .common import (
    build_config,
    client_from_env,
    is_uuid,
)
from .config import HMCConfig
from .ssh_commands import _ssh_lpar_name, _ssh_system_name

_T = TypeVar("_T")

mcp = FastMCP(
    name="hmc-mcp",
    instructions=(
        "Tools for querying and operating IBM Power systems via the HMC "
        "(Hardware Management Console) REST API. Managed systems are Power "
        "servers; logical partitions (LPARs) are the AIX/Linux/IBM i virtual "
        "servers running on them. Most read tools return parsed uom Atom "
        "entries as JSON.\n\n"
        "## Composite tools — prefer these for common tasks\n\n"
        "These tools aggregate multiple HMC endpoints in a single call and "
        "should be the first choice when their scope matches the task:\n\n"
        "- **hmc_lpar_summary(lpar_name_or_uuid)** — state, RMC status, "
        "memory/CPU, OS version, and adapter count for one LPAR. Use instead "
        "of hmc_lpars + hmc_list_adapters when you need a quick health check "
        "or status snapshot of a single partition.\n"
        "- **hmc_system_summary(system_name_or_uuid)** — system state, total "
        "resources, LPAR count and state breakdown, and VIOS state for one "
        "managed system. Use instead of hmc_systems + hmc_lpars + hmc_vios "
        "when you need an overview of a single server.\n"
        "- **hmc_capacity_report()** — total, assigned, and free memory (MiB) "
        "and processor units for every managed system, plus running/total LPAR "
        "counts. Use to survey available capacity across the whole HMC.\n"
        "- **hmc_find_placement(desired_memory_mb, desired_proc_units)** — "
        "returns systems that can host a new LPAR of the given size, sorted by "
        "free memory. Use before provisioning to choose a target system.\n"
        "- **hmc_recent_jobs(limit)** — most-recent HMC async jobs (power ops, "
        "firmware, migrations) on HMC versions that expose the global Job feed. "
        "Use to audit recent activity; poll a submitted job through its SELF link.\n"
        "- **hmc_provision_lpar(...)** — end-to-end LPAR creation: creates the "
        "partition, attaches a virtual network adapter, and optionally attaches "
        "a vSCSI disk. Supports dry_run=True to validate inputs without making "
        "changes. For lower-level creation without adapters, use hmc_create_lpar.\n\n"
        "## Recommended workflows\n\n"
        "**Check LPAR status:** hmc_lpar_summary → inspect state/rmc_state/os_version.\n\n"
        "**Survey a server:** hmc_system_summary → inspect system state, LPAR "
        "counts, and VIOS health in one call.\n\n"
        "**Plan new LPAR placement:** hmc_capacity_report (see all systems) → "
        "hmc_find_placement(mem, cpu) (filter to candidates) → pick system_uuid.\n\n"
        "**Provision a new LPAR:** hmc_find_placement → hmc_provision_lpar with "
        "dry_run=True (validate) → hmc_provision_lpar with dry_run=False (execute) "
        "→ hmc_lpar_summary (confirm).\n\n"
        "**Track an operation:** retain the job UUID and SELF link returned by the "
        "submitting tool → hmc_get_job(job_uuid, job_href=self_link) for detail → "
        "hmc_wait_for_job(job_uuid, job_href=self_link) to poll until done. Use "
        "hmc_recent_jobs only for HMC versions that support the global feed.\n\n"
        "## Lower-level tools\n\n"
        "Use the individual tools (hmc_systems, hmc_lpars, hmc_vios, "
        "hmc_list_adapters, etc.) when you need raw resource data, fields not "
        "returned by the composite tools, or operations outside the composite "
        "tool scope (network, storage, templates, metrics, users).\n\n"
        "## Multi-agent ownership protocol\n\n"
        "When multiple agents share this server, LPAR ownership is tracked via "
        "a description-field token: ``[hmc-mcp owner:<agent_id> created:<date>]``.\n\n"
        "**On create:** Ownership tokens are stamped automatically by "
        "hmc_create_lpar and hmc_provision_lpar. For hmc_deploy_partition_template, "
        "ownership stamping is manual for now — the deploy job result does not "
        "reliably surface the new LPAR name. After the job completes, resolve the "
        "LPAR name (e.g. via hmc_lpars or hmc_lpar_summary) and call "
        "hmc_set_lpar_description to write the ownership token.\n\n"
        "**Before delete / rename / description-overwrite:** Read the LPAR "
        "description with hmc_get_lpar_description or hmc_lpar_summary. If it "
        "contains ``[hmc-mcp owner:<id> ...]`` and <id> differs from your "
        "HMC_AGENT_ID, stop and ask the operator before proceeding.\n\n"
        "**Absent token:** An LPAR with no token was created before this feature "
        "or through a path that does not stamp. Treat it as unowned and proceed "
        "with caution — ask the operator if in doubt.\n\n"
        "**Set HMC_AGENT_ID** in the environment for per-agent attribution in "
        "HMC audit logs (X-Audit-Memento: hmc-mcp:<agent_id>)."
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
_STATE_CHANGING = ToolAnnotations(readOnlyHint=False)

READ_ONLY_TOOLS = frozenset(
    {
        "hmc_console_info",
        "hmc_systems",
        "hmc_system_summary",
        "hmc_lpars",
        "hmc_get_lpar",
        "hmc_get_lpar_state",
        "hmc_lpar_summary",
        "hmc_vios",
        "hmc_get_vios",
        "hmc_list_resources",
        "hmc_get_job",
        "hmc_recent_jobs",
        "hmc_capacity_report",
        "hmc_find_placement",
        "hmc_find_system",
        "hmc_wait_for_job",
        "hmc_list_adapters",
        "hmc_list_configured_hosts",
        "hmc_list_volume_groups",
        "hmc_list_virtual_switches",
        "hmc_list_virtual_networks",
        "hmc_list_network_bridges",
        "hmc_list_fc_ports",
        "hmc_list_sea_adapters",
        "hmc_partition_templates",
        "hmc_get_partition_template",
        "hmc_list_clusters",
        "hmc_shared_storage_pools",
        "hmc_get_pcm_preferences",
        "hmc_processed_metrics",
        "hmc_processed_metric_links",
        "hmc_aggregated_metrics",
        "hmc_aggregated_metric_links",
        "hmc_users",
        "hmc_get_user",
        "hmc_list_password_policies",
        "hmc_get_password_policy_status",
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
    }
)

DESTRUCTIVE_TOOLS = frozenset(
    {
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
        "hmc_backup_lpar_profiles",
    }
)


def _run(fn: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """Run a coroutine-returning closure from a sync tool function."""
    return asyncio.run(fn())


# ---------------------------------------------------------------------- #
# Name-or-UUID resolution (REST-first, SSH fallback) for SSH tools
# ---------------------------------------------------------------------- #


async def _system_name_from_rest(hmc: HMCClient, system_uuid: str) -> str:
    """Resolve a managed-system UUID to its CLI SystemName via REST."""
    entry = await hmc.get_managed_system(system_uuid)
    if not entry or "SystemName" not in entry.get("Resource", {}):
        raise ValueError(
            f"Could not resolve system UUID {system_uuid!r} to a system name. "
            "Use hmc_systems to find the system_uuid."
        )
    return entry["Resource"]["SystemName"]


async def _lpar_name_from_rest(hmc: HMCClient, lpar_uuid: str) -> str:
    """Resolve an LPAR UUID to its CLI PartitionName via REST."""
    entry = await hmc.get_logical_partition(lpar_uuid)
    if not entry or "PartitionName" not in entry.get("Resource", {}):
        raise ValueError(
            f"Could not resolve LPAR UUID {lpar_uuid!r} to a partition name. "
            "Use hmc_lpars to find the lpar_uuid."
        )
    return entry["Resource"]["PartitionName"]


async def _resolve_system_name(
    config: HMCConfig,
    system_name_or_uuid: str | None,
    profile: str | None = None,
) -> str | None:
    """Resolve a system name-or-uuid to a CLI SystemName.

    Names pass through untouched (no REST needed). UUIDs are resolved via
    REST, falling back to an ``lssyscfg`` name lookup over SSH only when the
    REST transport is unreachable (``httpx.HTTPError``). A REST 4xx/5xx
    (``HMCError``) is *not* a transport failure — REST answered and the UUID
    is unknown, so the error surfaces rather than falling back.

    *profile* selects the HMC connection profile for the REST lookup; when
    ``None`` the env-default resolution order is used (matching the behavior
    of all pre-#127 calls).
    """
    if system_name_or_uuid is None or not is_uuid(system_name_or_uuid):
        return system_name_or_uuid
    try:
        async with client_from_env(profile) as hmc:
            return await _system_name_from_rest(hmc, system_name_or_uuid)
    except httpx.HTTPError:
        return await _ssh_system_name(config, system_name_or_uuid)


async def _resolve_lpar_name(
    config: HMCConfig,
    lpar_name_or_uuid: str | None,
    system_name: str | None = None,
    profile: str | None = None,
) -> str | None:
    """Resolve an LPAR name-or-uuid to a CLI PartitionName.

    Same REST-first / SSH-fallback contract as :func:`_resolve_system_name`;
    *system_name* (when known) scopes the SSH name lookup to one system.
    *profile* selects the HMC connection profile for the REST lookup.
    """
    if lpar_name_or_uuid is None or not is_uuid(lpar_name_or_uuid):
        return lpar_name_or_uuid
    try:
        async with client_from_env(profile) as hmc:
            return await _lpar_name_from_rest(hmc, lpar_name_or_uuid)
    except httpx.HTTPError:
        return await _ssh_lpar_name(config, lpar_name_or_uuid, system_name)


@overload
def _ssh_with_client(
    fn: Callable[[HMCConfig, str, str], Awaitable[_T]],
    *,
    system_name_or_uuid: str,
    lpar_name_or_uuid: str,
    profile: str | None = None,
) -> _T: ...


@overload
def _ssh_with_client(
    fn: Callable[[HMCConfig, str, Literal[None]], Awaitable[_T]],
    *,
    system_name_or_uuid: str,
    lpar_name_or_uuid: None = None,
    profile: str | None = None,
) -> _T: ...


@overload
def _ssh_with_client(
    fn: Callable[[HMCConfig, Literal[None], str], Awaitable[_T]],
    *,
    system_name_or_uuid: None = None,
    lpar_name_or_uuid: str,
    profile: str | None = None,
) -> _T: ...


@overload
def _ssh_with_client(
    fn: Callable[[HMCConfig, Literal[None], Literal[None]], Awaitable[_T]],
    *,
    system_name_or_uuid: None = None,
    lpar_name_or_uuid: None = None,
    profile: str | None = None,
) -> _T: ...


def _ssh_with_client(
    fn: Callable[[HMCConfig, str | None, str | None], Awaitable[_T]],
    *,
    system_name_or_uuid: str | None = None,
    lpar_name_or_uuid: str | None = None,
    profile: str | None = None,
) -> _T:
    """Resolve name-or-uuid args to CLI names, then run an SSH tool body.

    Collapses the pervasive ``async def _go`` + name resolution + ``_run``
    scaffold in the SSH-passthrough tools, mirroring :func:`with_client` for
    the SSH seam. *system_name_or_uuid* and *lpar_name_or_uuid* may each be a
    CLI name (passed through untouched) or a UUID (resolved via REST, falling
    back to an ``lssyscfg`` name lookup over SSH when the REST transport is
    unreachable). *fn* is called with the profile-selected ``HMCConfig``
    followed by the resolved system and LPAR CLI names (``None`` when the
    matching arg was not supplied) and returns an awaitable for the tool result
    — a ``run_hmc_command(...)`` call, or a call into an :mod:`ssh` helper that
    runs the command itself.

    *profile* selects the HMC connection profile; when ``None`` the env-default
    resolution order is used.  The same profile is used for both SSH and the
    REST name-resolution leg so that both operations target the same HMC.
    Selection is local to this call with no cross-call shared state.
    """

    async def _go() -> _T:
        config = build_config(profile=profile)
        system_name = await _resolve_system_name(config, system_name_or_uuid, profile)
        lpar_name = await _resolve_lpar_name(
            config, lpar_name_or_uuid, system_name, profile
        )
        return await fn(config, system_name, lpar_name)

    return _run(_go)
