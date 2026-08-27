"""hmc-mcp command line interface.

Usage examples:
    hmc-mcp serve --access-policy NAME # run the MCP server over stdio
    hmc-mcp systems list              # list managed systems as a table
    hmc-mcp lpars list --json         # list LPARs as JSON
    hmc-mcp lpars show mylpar         # find an LPAR by name and show it
    hmc-mcp console info              # HMC version / connectivity check

This module is a thin aggregator: the command groups live in
domain modules in :mod:`hmc_mcp.cli_commands` that register themselves on the
shared :class:`typer.Typer` in :mod:`hmc_mcp.cli_commands.app`.
"""

from __future__ import annotations

from .cli_commands.app import (
    GlobalOpts as GlobalOpts,
    _ssh_config as _ssh_config,
    app as app,
    console as console,
    main as main,
)

from .cli_commands import adapters  # noqa: F401  (registers commands)
from .cli_commands import cluster  # noqa: F401  (registers commands)
from .cli_commands import config  # noqa: F401  (registers commands)
from .cli_commands import console as _console_commands  # noqa: F401
from .cli_commands import jobs  # noqa: F401  (registers commands)
from .cli_commands import lpars  # noqa: F401  (registers commands)
from .cli_commands import lpars_inventory  # noqa: F401  (registers commands)
from .cli_commands import lpars_migration  # noqa: F401  (registers commands)
from .cli_commands import memory_pools  # noqa: F401  (registers commands)
from .cli_commands import metrics  # noqa: F401  (registers commands)
from .cli_commands import network  # noqa: F401  (registers commands)
from .cli_commands import raw  # noqa: F401  (registers commands)
from .cli_commands import snapshot  # noqa: F401  (registers commands)
from .cli_commands import storage  # noqa: F401  (registers commands)
from .cli_commands import systems  # noqa: F401  (registers commands)
from .cli_commands import templates  # noqa: F401  (registers commands)
from .cli_commands import vios  # noqa: F401  (registers commands)
