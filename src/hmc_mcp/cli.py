"""hmc-mcp command line interface.

Usage examples:
    hmc-mcp serve --access-policy NAME # run the MCP server over stdio
    hmc-mcp systems list              # list managed systems as a table
    hmc-mcp lpars list --json         # list LPARs as JSON
    hmc-mcp lpars show mylpar         # find an LPAR by name and show it
    hmc-mcp console info              # HMC version / connectivity check

This module is the explicit composition root: ``cli_commands.app`` owns command
groups, while domain modules expose registration functions called here to build
the complete tree.
"""

from __future__ import annotations

from .cli_commands.app import (
    adapters_app,
    app as app,
    cluster_app,
    config_app,
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
from .cli_commands.output import console as console
from .cli_commands.runtime import GlobalOpts as GlobalOpts

from .cli_commands import (
    adapters,
    cluster,
    config,
    console as console_commands,
    jobs,
    memory_pools,
    metrics,
    network,
    pcie,
    raw,
    snapshot,
    storage,
    systems,
    templates,
    vnic,
    vios,
    vios_labels,
)
from .cli_commands.lpar import (
    config as lpar_config,
    create as lpar_create,
    decommission as lpar_decommission,
    inventory as lpar_inventory,
    lifecycle as lpar_lifecycle,
    migration as lpar_migration,
    modify as lpar_modify,
    profiles as lpar_profiles,
    provision as lpar_provision,
)


def _register_commands() -> None:
    """Compose the complete command tree from explicit domain registrations."""
    registrations = (
        (adapters, adapters_app),
        (cluster, cluster_app),
        (config, config_app),
        (console_commands, console_app),
        (jobs, jobs_app),
        (lpar_config, lpars_app),
        (lpar_create, lpars_app),
        (lpar_decommission, lpars_app),
        (lpar_inventory, lpars_app),
        (lpar_lifecycle, lpars_app),
        (lpar_migration, lpars_app),
        (lpar_modify, lpars_app),
        (lpar_profiles, lpars_app),
        (lpar_provision, lpars_app),
        (memory_pools, memory_pools_app),
        (metrics, metrics_app),
        (network, network_app),
        (pcie, network_app),
        (raw, raw_app),
        (snapshot, snapshot_app),
        (storage, storage_app),
        (systems, systems_app),
        (templates, templates_app),
        (vnic, network_app),
        (vios, vios_app),
        (vios_labels, vios_app),
    )
    for module, group in registrations:
        module.register_commands(group)


_register_commands()
