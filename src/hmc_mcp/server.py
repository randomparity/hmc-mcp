"""MCP server exposing the IBM HMC REST API as MCP tools.

Run:
    hmc-mcp serve            # stdio transport (default, for agents)
    hmc-mcp serve --http     # streamable HTTP on 127.0.0.1:8000

The HTTP transport is UNAUTHENTICATED: it exposes every enabled tool,
including user administration, to anyone who can reach the port. Bind only
to loopback (the default). ``hmc-mcp serve --http`` refuses to bind beyond
loopback unless ``--allow-remote`` is passed; even then, gate the endpoint
with an authenticated reverse proxy (MCP gateway or HTTPS proxy with
bearer-token auth). Never expose it directly on a network. The arbitrary
``hmc_run_command`` escape hatch is disabled unless serve is started with
``--enable-arbitrary-command``.

Authentication:
    REST tools authenticate via HMC_USER/HMC_PASSWORD (see
    ``client_from_env``). SSH-passthrough tools (those that run HMC CLI
    commands via ``run_hmc_command``) use the same env-var configuration as
    ``hmc_run_command``: set HMC_SSH_KEY_FILE for key-based auth, otherwise
    HMC_PASSWORD is used.

Addressing:
    Public tools generally accept a resource name or UUID where their parameter
    is named ``*_name_or_uuid``. Parameters explicitly named ``*_uuid`` require
    a UUID. SSH-passthrough tools resolve UUIDs to CLI names before running the
    HMC command.

This module is a thin aggregator: the tool handlers live in domain
submodules (``server_lpars``, ``server_storage``, ...). ``create_mcp``
explicitly registers each domain on a fresh application instance.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import sys
from collections.abc import Callable, Mapping

from fastmcp import FastMCP

from ._app import (
    create_mcp as _create_base_mcp,
)
from .access_policy import AccessPolicy, resolve_access_policy_path
from .dispatch_scope import dispatch_authorizer
from .tool_registry import Authorize, ToolSecurity, build_tool_security
from . import (
    server_adapters,
    server_capacity,
    server_composite,
    server_health,
    server_jobs,
    server_lpar_config,
    server_lpars,
    server_lpm,
    server_metrics,
    server_network,
    server_profiles,
    server_provision,
    server_storage,
    server_system_resources,
    server_systems,
    server_templates,
    server_updates,
    server_users,
    server_vios,
)

from .server_systems import (
    hmc_console_info as hmc_console_info,
    hmc_get_system as hmc_get_system,
    hmc_get_lpar as hmc_get_lpar,
    hmc_get_lpar_state as hmc_get_lpar_state,
    hmc_get_vios as hmc_get_vios,
    hmc_list_configured_hosts as hmc_list_configured_hosts,
    hmc_list_resources as hmc_list_resources,
    hmc_list_lpars as hmc_list_lpars,
    hmc_modify_system as hmc_modify_system,
    hmc_power_off_system as hmc_power_off_system,
    hmc_power_on_system as hmc_power_on_system,
    hmc_list_systems as hmc_list_systems,
    hmc_list_vios as hmc_list_vios,
)
from .server_capacity import (
    hmc_capacity_report as hmc_capacity_report,
    hmc_find_placement as hmc_find_placement,
)
from .server_command import (
    HMC_RUN_COMMAND_SECURITY,
    hmc_run_command as hmc_run_command,
    configure_arbitrary_command_tool,
)
from .server_jobs import (
    hmc_get_job as hmc_get_job,
    hmc_list_recent_jobs as hmc_list_recent_jobs,
    hmc_wait_for_job as hmc_wait_for_job,
)
from .server_health import hmc_fleet_health as hmc_fleet_health
from .server_permissions import (
    EFFECTIVE_PERMISSIONS_SECURITY,
    TOOL_NAME as PERMISSIONS_TOOL_NAME,
    register_permissions_tool,
)

from .server_lpars import (
    hmc_create_lpar as hmc_create_lpar,
    hmc_decommission_lpar as hmc_decommission_lpar,
    hmc_delete_lpar as hmc_delete_lpar,
    hmc_dlpar_mem as hmc_dlpar_mem,
    hmc_dlpar_proc as hmc_dlpar_proc,
    hmc_modify_lpar as hmc_modify_lpar,
    hmc_rename_lpar as hmc_rename_lpar,
    hmc_power_off_lpar as hmc_power_off_lpar,
    hmc_power_on_lpar as hmc_power_on_lpar,
)
from .server_vios import (
    hmc_backup_vios as hmc_backup_vios,
    hmc_create_vios as hmc_create_vios,
    hmc_delete_vios as hmc_delete_vios,
    hmc_install_lpar_os as hmc_install_lpar_os,
    hmc_install_vios as hmc_install_vios,
    hmc_list_vios_backups as hmc_list_vios_backups,
    hmc_power_off_vios as hmc_power_off_vios,
    hmc_power_on_vios as hmc_power_on_vios,
    hmc_restore_vios as hmc_restore_vios,
)
from .server_adapters import (
    hmc_add_network_adapter as hmc_add_network_adapter,
    hmc_add_vfc_adapter as hmc_add_vfc_adapter,
    hmc_add_vscsi_adapter as hmc_add_vscsi_adapter,
    hmc_delete_adapter as hmc_delete_adapter,
    hmc_list_adapters as hmc_list_adapters,
)
from .server_storage import (
    hmc_attach_disk_to_lpar as hmc_attach_disk_to_lpar,
    hmc_create_logical_unit as hmc_create_logical_unit,
    hmc_create_media_repository as hmc_create_media_repository,
    hmc_create_optical_media as hmc_create_optical_media,
    hmc_create_virtual_disk as hmc_create_virtual_disk,
    hmc_create_volume_group as hmc_create_volume_group,
    hmc_delete_logical_unit as hmc_delete_logical_unit,
    hmc_delete_media_repository as hmc_delete_media_repository,
    hmc_get_shared_storage_pool as hmc_get_shared_storage_pool,
    hmc_list_clusters as hmc_list_clusters,
    hmc_list_volume_groups as hmc_list_volume_groups,
    hmc_map_storage_to_lpar as hmc_map_storage_to_lpar,
    hmc_list_shared_storage_pools as hmc_list_shared_storage_pools,
)
from .server_network import (
    hmc_add_vnic as hmc_add_vnic,
    hmc_create_virtual_network as hmc_create_virtual_network,
    hmc_delete_virtual_network as hmc_delete_virtual_network,
    hmc_list_fc_ports as hmc_list_fc_ports,
    hmc_list_network_bridges as hmc_list_network_bridges,
    hmc_list_sea_adapters as hmc_list_sea_adapters,
    hmc_list_virtual_networks as hmc_list_virtual_networks,
    hmc_list_virtual_switches as hmc_list_virtual_switches,
    hmc_list_vnics as hmc_list_vnics,
    hmc_remove_vnic as hmc_remove_vnic,
    hmc_set_sriov_adapter_mode as hmc_set_sriov_adapter_mode,
)
from .server_lpm import (
    hmc_migrate_abort_lpar as hmc_migrate_abort_lpar,
    hmc_migrate_lpar as hmc_migrate_lpar,
    hmc_migrate_recover_lpar as hmc_migrate_recover_lpar,
    hmc_migrate_validate_lpar as hmc_migrate_validate_lpar,
    hmc_remote_restart_lpar as hmc_remote_restart_lpar,
)
from .server_templates import (
    hmc_deploy_partition_template as hmc_deploy_partition_template,
    hmc_get_partition_template as hmc_get_partition_template,
    hmc_list_partition_templates as hmc_list_partition_templates,
)
from .server_metrics import (
    hmc_aggregated_metric_links as hmc_aggregated_metric_links,
    hmc_aggregated_metrics as hmc_aggregated_metrics,
    hmc_get_pcm_preferences as hmc_get_pcm_preferences,
    hmc_processed_metric_links as hmc_processed_metric_links,
    hmc_processed_metrics as hmc_processed_metrics,
    hmc_set_pcm_preferences as hmc_set_pcm_preferences,
)
from .server_users import (
    hmc_configure_ldap as hmc_configure_ldap,
    hmc_create_password_policy as hmc_create_password_policy,
    hmc_create_user as hmc_create_user,
    hmc_delete_password_policy as hmc_delete_password_policy,
    hmc_delete_user as hmc_delete_user,
    hmc_get_ldap_config as hmc_get_ldap_config,
    hmc_list_password_policy_status as hmc_list_password_policy_status,
    hmc_get_user as hmc_get_user,
    hmc_list_password_policies as hmc_list_password_policies,
    hmc_modify_password_policy as hmc_modify_password_policy,
    hmc_modify_user as hmc_modify_user,
    hmc_remove_ldap_config as hmc_remove_ldap_config,
    hmc_list_users as hmc_list_users,
)
from .server_updates import (
    hmc_get_available_hmc_ptfs as hmc_get_available_hmc_ptfs,
    hmc_update_console_software as hmc_update_console_software,
    hmc_update_firmware as hmc_update_firmware,
    hmc_vios_update as hmc_vios_update,
)
from .server_profiles import (
    hmc_assign_profile_io_slot as hmc_assign_profile_io_slot,
    hmc_backup_lpar_profiles as hmc_backup_lpar_profiles,
    hmc_restore_lpar_profiles as hmc_restore_lpar_profiles,
    hmc_sync_lpar_profile as hmc_sync_lpar_profile,
)
from .server_lpar_config import (
    hmc_get_lpar_description as hmc_get_lpar_description,
    hmc_get_lpar_msp as hmc_get_lpar_msp,
    hmc_get_lpar_proc_compat as hmc_get_lpar_proc_compat,
    hmc_set_lpar_description as hmc_set_lpar_description,
    hmc_set_lpar_msp as hmc_set_lpar_msp,
    hmc_set_lpar_proc_compat as hmc_set_lpar_proc_compat,
)
from .server_system_resources import (
    hmc_get_proc_compat_modes as hmc_get_proc_compat_modes,
    hmc_list_io_slots as hmc_list_io_slots,
    hmc_list_memory_pools as hmc_list_memory_pools,
    hmc_remove_memory_pool as hmc_remove_memory_pool,
)
from .server_composite import (
    hmc_lpar_summary as hmc_lpar_summary,
    hmc_system_summary as hmc_system_summary,
)
from .server_provision import (
    hmc_provision_lpar as hmc_provision_lpar,
)

TOOL_MODULES = (
    server_systems,
    server_capacity,
    server_jobs,
    server_health,
    server_lpars,
    server_vios,
    server_adapters,
    server_storage,
    server_network,
    server_lpm,
    server_templates,
    server_metrics,
    server_users,
    server_updates,
    server_profiles,
    server_lpar_config,
    server_system_resources,
    server_composite,
    server_provision,
)


TOOL_SECURITY: Mapping[str, ToolSecurity] = build_tool_security(
    [module.tool_security() for module in TOOL_MODULES],
    {
        "hmc_run_command": HMC_RUN_COMMAND_SECURITY,
        "hmc_effective_permissions": EFFECTIVE_PERMISSIONS_SECURITY,
    },
)


def _gates(
    policy: AccessPolicy | None,
) -> tuple[Callable[[str], bool] | None, Authorize | None]:
    """The registration-time and dispatch-time questions *policy* answers.

    Derived together and always passed together: a site given one without the
    other registers tools it does not authorize, which is the drift ADR 0038's
    registry assertion exists to catch.
    """
    if policy is None:
        return None, None
    return policy.permits_tool, dispatch_authorizer(policy)


def create_mcp(policy: AccessPolicy | None = None) -> FastMCP:
    """Compose a fresh MCP application bounded by *policy*.

    ``None`` applies no ceiling, authorizes no connection, and registers every
    tool — the behaviour before ADR 0037, and what every deployment gets until
    #225 makes startup fail closed. Both gates are passed to each registration
    site rather than checked here, so no site can be given a policy it does not
    apply; ADR 0038's registry assertion is what checks that it did.
    """
    permits, authorize = _gates(policy)
    application = _create_base_mcp()
    for module in TOOL_MODULES:
        module.register_tools(application, permits=permits, authorize=authorize)
    register_permissions_tool(
        application, policy, TOOL_SECURITY, permits=permits, authorize=authorize
    )
    return application


mcp = create_mcp()


def _unselected_policy_file() -> str | None:
    """The platform-native policy file's path when one exists, else ``None``.

    Reads nothing and never raises: ``resolve_access_policy_path`` reaches
    ``Path.home()``, which raises under a uid with no passwd entry and no HOME,
    and a diagnostic that can abort a start nobody asked to constrain is worse
    than no diagnostic.
    """
    try:
        path = resolve_access_policy_path()
        return str(path) if path.exists() else None
    except (RuntimeError, OSError, ValueError):
        return None


def _startup_warnings(
    tool_count: int,
    access_policy: AccessPolicy | None,
    enable_arbitrary_command: bool,
) -> tuple[str, ...]:
    """The stderr lines describing what this server will and will not expose.

    Every input exists only here — the served registry, the policy, and the
    escape-hatch flag — which is why the four warnings share one function. An
    empty surface already implies the inspection tool is absent, so it replaces
    that line rather than printing beside it.
    """
    lines: list[str] = []
    if tool_count == 0:
        lines.append(
            "warning: this server exposes no tools. Nothing it is asked to do will "
            "succeed."
        )
    elif access_policy is not None and not access_policy.permits_tool(
        PERMISSIONS_TOOL_NAME
    ):
        lines.append(
            f"warning: access policy {access_policy.name!r} withholds "
            f"{PERMISSIONS_TOOL_NAME}, so this server cannot report its own "
            "effective permissions to a client."
        )
    if access_policy is None and (path := _unselected_policy_file()) is not None:
        lines.append(
            f"warning: {path} exists but no access policy was selected, so "
            "neither a capability ceiling nor connection-scope authorization is "
            "applied. Pass --access-policy NAME to enforce one."
        )
    if (
        enable_arbitrary_command
        and access_policy is not None
        and not access_policy.permits_tool("hmc_run_command")
    ):
        lines.append(
            "warning: --enable-arbitrary-command was requested, but access policy "
            f"{access_policy.name!r} does not grant hmc_run_command, so it is not "
            "exposed. Name it in a grant's tools to allow it."
        )
    return tuple(lines)


def _warn(lines: tuple[str, ...]) -> None:
    """Write startup diagnostics to stderr, or to nowhere at all.

    Never to stdout, which carries JSON-RPC framing under the stdio transport.
    ``print(file=None)`` falls back to ``sys.stdout``, and CPython sets
    ``sys.stderr`` to ``None`` when fd 2 is not open at interpreter start — a
    launcher closing it (``serve 2>&-``) would otherwise inject warning text
    into the protocol stream — so an absent stream drops the lines instead.

    Nor may emitting one abort a start, which is what ``_unselected_policy_file``
    already refuses for resolving one. A broken stream raises ``OSError`` and a
    closed one raises ``ValueError``, so both are caught.
    """
    stream = sys.stderr
    if stream is None:
        return
    try:
        for line in lines:
            print(line, file=stream)
    except (OSError, ValueError):
        pass


def _serve_application(
    enable_arbitrary_command: bool, access_policy: AccessPolicy | None
) -> FastMCP:
    """Compose, gate, and diagnose the application about to be served."""
    application = create_mcp(access_policy)
    permits, authorize = _gates(access_policy)

    async def _prepare() -> int:
        await configure_arbitrary_command_tool(
            enable_arbitrary_command,
            application,
            permits=permits,
            authorize=authorize,
        )
        return len(await application.local_provider.list_tools())

    tool_count = asyncio.run(_prepare())
    _warn(_startup_warnings(tool_count, access_policy, enable_arbitrary_command))
    return application


def main_stdio(
    enable_arbitrary_command: bool = False,
    access_policy: AccessPolicy | None = None,
) -> None:
    """Start an MCP server over stdio, bounded by *access_policy*."""
    _serve_application(enable_arbitrary_command, access_policy).run()


def main_http(
    host: str = "127.0.0.1",
    port: int = 8000,
    enable_arbitrary_command: bool = False,
    allow_remote: bool = False,
    access_policy: AccessPolicy | None = None,
) -> None:
    """Start an MCP server over streamable HTTP, bounded by *access_policy*."""
    if not allow_remote and not _is_loopback(host):
        raise ValueError(
            f"listen host {host!r} binds beyond loopback, but the streamable HTTP "
            "server has no authentication and exposes every enabled tool "
            "(including user administration). Refusing to start. Explicitly "
            "authorize remote binding and put an authenticated reverse proxy in front."
        )
    _serve_application(enable_arbitrary_command, access_policy).run(
        transport="streamable-http", host=host, port=port
    )


def _is_loopback(host: str) -> bool:
    """Return true only when every resolved bind address is an IP loopback."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for family, _, _, _, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            return False
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return False
        if not address.is_loopback:
            return False
    return True
