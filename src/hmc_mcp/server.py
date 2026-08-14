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
submodules (``server_lpars``, ``server_storage``, ...) that register
themselves on the shared FastMCP instance in ``._app`` when imported here.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket

from ._app import (
    DESTRUCTIVE_TOOLS as DESTRUCTIVE_TOOLS,
    READ_ONLY_TOOLS as READ_ONLY_TOOLS,
    mcp as mcp,
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
    hmc_run_command as hmc_run_command,
    configure_arbitrary_command_tool,
)
from .server_jobs import (
    hmc_get_job as hmc_get_job,
    hmc_list_recent_jobs as hmc_list_recent_jobs,
    hmc_wait_for_job as hmc_wait_for_job,
)

from .server_lpars import (
    hmc_create_lpar as hmc_create_lpar,
    hmc_delete_lpar as hmc_delete_lpar,
    hmc_dlpar_mem as hmc_dlpar_mem,
    hmc_dlpar_proc as hmc_dlpar_proc,
    hmc_modify_lpar as hmc_modify_lpar,
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
    hmc_get_password_policy_status as hmc_get_password_policy_status,
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


def main_stdio(enable_arbitrary_command: bool = False) -> None:
    """Start the fully composed MCP server over stdio."""
    asyncio.run(configure_arbitrary_command_tool(enable_arbitrary_command))
    mcp.run()


def main_http(
    host: str = "127.0.0.1",
    port: int = 8000,
    enable_arbitrary_command: bool = False,
    allow_remote: bool = False,
) -> None:
    """Start the fully composed MCP server over streamable HTTP."""
    if not allow_remote and not _is_loopback(host):
        raise ValueError(
            f"listen host {host!r} binds beyond loopback, but the streamable HTTP "
            "server has no authentication and exposes every enabled tool "
            "(including user administration). Refusing to start. Explicitly "
            "authorize remote binding and put an authenticated reverse proxy in front."
        )
    asyncio.run(configure_arbitrary_command_tool(enable_arbitrary_command))
    mcp.run(transport="streamable-http", host=host, port=port)


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
