"""Shared app state and entry points for the hmc-mcp server.

Builds empty :class:`FastMCP` instances for explicit composition, defines
the capability annotations and frozensets that document them, and provides
the small sync-run / SSH-passthrough helpers used by tool bodies.

``server.py`` imports this module and every ``server_*`` domain module; the
domain modules import ``mcp`` back from here (one-way dependency, no
cycles).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from functools import partial
from typing import Any, Literal, TypeVar, overload

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .common import build_config
from .config import HMCConfig
from .ssh_selectors import resolve_ssh_names

_T = TypeVar("_T")

_new_mcp = partial(
    FastMCP,
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
        "of hmc_list_lpars + hmc_list_adapters when you need a quick health check "
        "or status snapshot of a single partition.\n"
        "- **hmc_system_summary(system_name_or_uuid)** — system state, total "
        "resources, LPAR count and state breakdown, and VIOS state for one "
        "managed system. Use instead of hmc_list_systems + hmc_list_lpars + hmc_list_vios "
        "when you need an overview of a single server.\n"
        "- **hmc_capacity_report()** — total, assigned, and free memory (MiB) "
        "and processor units for every managed system, plus running/total LPAR "
        "counts. Use to survey available capacity across the whole HMC.\n"
        "- **hmc_fleet_health()** — exception-only estate view covering systems "
        "not operating, VIOS not running, inactive LPAR RMC, and recent failed "
        "jobs. Use for a bounded fleet health check instead of composing raw lists.\n"
        "- **hmc_find_placement(desired_memory_mb, desired_proc_units)** — "
        "returns systems that can host a new LPAR of the given size, sorted by "
        "free memory. Use before provisioning to choose a target system.\n"
        "- **hmc_list_recent_jobs(limit)** — most-recent HMC async jobs (power ops, "
        "firmware, migrations) on HMC versions that expose the global Job feed. "
        "Use to audit recent activity; poll a submitted job through its SELF link.\n"
        "- **hmc_provision_lpar(...)** — end-to-end LPAR creation: creates the "
        "partition, attaches a virtual network adapter, and optionally attaches "
        "a vSCSI disk. Supports dry_run=True to validate inputs without making "
        "changes. For lower-level creation without adapters, use hmc_create_lpar.\n\n"
        "## Resource addressing and asynchronous jobs\n\n"
        "Parameters ending in `*_name_or_uuid` accept either a resource name or UUID. "
        "Parameters\nending in `*_uuid` require a UUID. SSH-passthrough tools resolve "
        "UUIDs to HMC CLI names\nbefore running the command.\n\n"
        "For tools that expose asynchronous wait controls, `wait=False` is the default "
        "and returns\nthe submitted job for later polling; `wait=True` polls until the "
        "job reaches a terminal\nstate. Install tools use `wait_timeout_seconds=None` "
        "by default, deriving the client-side\npolling budget from "
        "`hmc_timeout_minutes` plus one poll interval. Other wait-capable tools\nuse "
        "`timeout_seconds=300` by default. `poll_interval=5` is the default number of "
        "seconds\nbetween status requests.\n\n"
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
        "hmc_list_recent_jobs only for HMC versions that support the global feed.\n\n"
        "**Migrate safely:** hmc_migrate_lpar validates to a successful terminal "
        "state before migration by default; set validate_first=False only to "
        "request direct submission explicitly.\n\n"
        "## Lower-level tools\n\n"
        "Use the individual tools (hmc_list_systems, hmc_list_lpars, hmc_list_vios, "
        "hmc_list_adapters, etc.) when you need raw resource data, fields not "
        "returned by the composite tools, or operations outside the composite "
        "tool scope (network, storage, templates, metrics, users).\n\n"
        "## Multi-agent ownership protocol\n\n"
        "When multiple agents share this server, LPAR ownership is tracked via "
        "a description-field token: ``[hmc-mcp owner:<agent_id> created:<date>]``.\n\n"
        "**On create:** Ownership tokens are stamped automatically by "
        "hmc_create_lpar and hmc_provision_lpar. A completed "
        "hmc_deploy_partition_template call also stamps automatically when wait=True "
        "and its before/after LPAR snapshots identify exactly one new partition. If "
        "ownership_stamped is None, follow the returned warning: resolve the LPAR name "
        "(e.g. via hmc_list_lpars or hmc_lpar_summary) and call "
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


def create_mcp() -> FastMCP:
    """Create an empty MCP application for explicit domain composition."""
    return _new_mcp()


# Tool capability annotations. MCP clients and gateways use these to separate
# read-only, state-changing, and destructive tools (e.g. to auto-approve the
# first, warn before the last). Tools are tagged inline on their @tool
# collector with _READ_ONLY or _DESTRUCTIVE; everything else is state-changing
# and intentionally untagged. tests/test_capabilities.py asserts the live tool
# registry matches the documented READ_ONLY_TOOLS / DESTRUCTIVE_TOOLS sets, so
# a new tool must be placed in exactly one category there and tagged here.
_READ_ONLY = ToolAnnotations(readOnlyHint=True)
_DESTRUCTIVE = ToolAnnotations(destructiveHint=True)
_STATE_CHANGING = ToolAnnotations(readOnlyHint=False)

READ_ONLY_TOOLS = frozenset(
    {
        "hmc_console_info",
        "hmc_list_systems",
        "hmc_system_summary",
        "hmc_list_lpars",
        "hmc_get_lpar",
        "hmc_get_lpar_state",
        "hmc_lpar_summary",
        "hmc_list_vios",
        "hmc_get_vios",
        "hmc_list_resources",
        "hmc_get_job",
        "hmc_list_recent_jobs",
        "hmc_fleet_health",
        "hmc_capacity_report",
        "hmc_find_placement",
        "hmc_get_system",
        "hmc_wait_for_job",
        "hmc_list_adapters",
        "hmc_list_configured_hosts",
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
        "hmc_processed_metrics",
        "hmc_processed_metric_links",
        "hmc_aggregated_metrics",
        "hmc_aggregated_metric_links",
        "hmc_list_users",
        "hmc_get_user",
        "hmc_list_password_policies",
        "hmc_list_password_policy_status",
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
        "hmc_get_media_repository",
        "hmc_list_optical_media",
        "hmc_list_storage_mappings",
    }
)

DESTRUCTIVE_TOOLS = frozenset(
    {
        "hmc_power_off_lpar",
        "hmc_delete_lpar",
        "hmc_decommission_lpar",
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
        "hmc_backup_lpar_profiles",
        "hmc_sync_lpar_profile",
        "hmc_detach_storage_mapping",
    }
)


def _run(fn: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """Run a coroutine-returning closure from a sync tool function."""
    return asyncio.run(fn())


def _run_limited_collection(
    fn: Callable[[], Coroutine[Any, Any, list[_T]]],
    limit: int | None,
) -> list[_T]:
    """Run a full collection request, then cap its agent-facing result."""
    if limit is not None and limit < 0:
        raise ValueError("limit must be greater than or equal to 0")
    entries = _run(fn)
    return entries if limit is None else entries[:limit]


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
        system_name, lpar_name = await resolve_ssh_names(
            config, system_name_or_uuid, lpar_name_or_uuid
        )
        return await fn(config, system_name, lpar_name)

    return _run(_go)
