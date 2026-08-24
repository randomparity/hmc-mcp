"""hmc-mcp command line interface.

Usage examples:
    hmc-mcp serve --access-policy NAME # run the MCP server over stdio
    hmc-mcp systems list              # list managed systems as a table
    hmc-mcp lpars list --json         # list LPARs as JSON
    hmc-mcp lpars show mylpar         # find an LPAR by name and show it
    hmc-mcp console info              # HMC version / connectivity check

This module is a thin aggregator: the command groups live in
domain submodules (``cli_systems``, ``cli_lpars``, ...) that register
themselves on the shared :class:`typer.Typer` in ``cli_app``.
"""

from __future__ import annotations

from .cli_app import (
    GlobalOpts as GlobalOpts,
    _ssh_config as _ssh_config,
    app as app,
    console as console,
    main as main,
)

from . import cli_adapters  # noqa: F401  (side-effect: registers commands)
from . import cli_cluster  # noqa: F401  (side-effect: registers commands)
from . import cli_console  # noqa: F401  (side-effect: registers commands)
from . import cli_jobs  # noqa: F401  (side-effect: registers commands)
from . import cli_lpars  # noqa: F401  (side-effect: registers commands)
from . import cli_memory_pools  # noqa: F401  (side-effect: registers commands)
from . import cli_metrics  # noqa: F401  (side-effect: registers commands)
from . import cli_network  # noqa: F401  (side-effect: registers commands)
from . import cli_raw  # noqa: F401  (side-effect: registers commands)
from . import cli_snapshot  # noqa: F401  (side-effect: registers commands)
from . import cli_storage  # noqa: F401  (side-effect: registers commands)
from . import cli_systems  # noqa: F401  (side-effect: registers commands)
from . import cli_templates  # noqa: F401  (side-effect: registers commands)
from . import cli_vios  # noqa: F401  (side-effect: registers commands)
from . import cli_config  # noqa: F401  (side-effect: registers commands)
