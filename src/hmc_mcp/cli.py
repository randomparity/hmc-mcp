"""hmc-mcp command line interface.

Usage examples:
    hmc-mcp serve --access-policy NAME # run the MCP server over stdio
    hmc-mcp systems list              # list managed systems as a table
    hmc-mcp lpars list --json         # list LPARs as JSON
    hmc-mcp lpars show mylpar         # find an LPAR by name and show it
    hmc-mcp console info              # HMC version / connectivity check

This module is the explicit composition root: command groups and shared plumbing
live in :mod:`hmc_mcp.cli_commands.app`, while domain modules expose registration
functions called here to build the complete tree.
"""

from __future__ import annotations

from .cli_commands.app import (
    GlobalOpts as GlobalOpts,
    adapters_app,
    app as app,
    cluster_app,
    config_app,
    console as console,
    console_app,
    jobs_app,
    lpars_app,
    main as main,
    memory_pools_app,
    metrics_app,
    network_app,
    raw_app,
    snapshot_app,
    storage_app,
    systems_app,
    templates_app,
    vios_app,
)

from .cli_commands import (
    adapters,
    cluster,
    config,
    console as console_commands,
    jobs,
    lpars_config,
    lpars_create,
    lpars_decommission,
    lpars_inventory,
    lpars_lifecycle,
    lpars_migration,
    lpars_modify,
    lpars_profiles,
    lpars_provision,
    memory_pools,
    metrics,
    network,
    raw,
    snapshot,
    storage,
    systems,
    templates,
    vios,
)


def _register_commands() -> None:
    """Compose the complete command tree from explicit domain registrations."""
    registrations = (
        (adapters, adapters_app),
        (cluster, cluster_app),
        (config, config_app),
        (console_commands, console_app),
        (jobs, jobs_app),
        (lpars_config, lpars_app),
        (lpars_create, lpars_app),
        (lpars_decommission, lpars_app),
        (lpars_inventory, lpars_app),
        (lpars_lifecycle, lpars_app),
        (lpars_migration, lpars_app),
        (lpars_modify, lpars_app),
        (lpars_profiles, lpars_app),
        (lpars_provision, lpars_app),
        (memory_pools, memory_pools_app),
        (metrics, metrics_app),
        (network, network_app),
        (raw, raw_app),
        (snapshot, snapshot_app),
        (storage, storage_app),
        (systems, systems_app),
        (templates, templates_app),
        (vios, vios_app),
    )
    for module, group in registrations:
        module.register_commands(group)


_register_commands()
