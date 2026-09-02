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

from .cli_commands import (
    adapters,
    cluster,
    config,
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
    vios,
    vios_labels,
    vnic,
)
from .cli_commands import (
    console as console_commands,
)
from .cli_commands.app import (
    adapters_app,
    cluster_app,
    config_app,
    console_app,
    jobs_app,
    lpars_app,
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
from .cli_commands.app import (
    app as app,
)
from .cli_commands.app import (
    main as main,
)
from .cli_commands.lpar import (
    config as lpar_config,
)
from .cli_commands.lpar import (
    create as lpar_create,
)
from .cli_commands.lpar import (
    decommission as lpar_decommission,
)
from .cli_commands.lpar import (
    inventory as lpar_inventory,
)
from .cli_commands.lpar import (
    lifecycle as lpar_lifecycle,
)
from .cli_commands.lpar import (
    migration as lpar_migration,
)
from .cli_commands.lpar import (
    modify as lpar_modify,
)
from .cli_commands.lpar import (
    profiles as lpar_profiles,
)
from .cli_commands.lpar import (
    provision as lpar_provision,
)
from .cli_commands.output import console as console
from .cli_commands.runtime import GlobalOpts as GlobalOpts


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
