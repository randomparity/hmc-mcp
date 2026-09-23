"""Immutable security catalog for every MCP tool shipped by this package."""

from __future__ import annotations

from collections.abc import Mapping

from hmcpctl.server_tools import (
    console,
    jobs,
    snapshot,
    updates,
)
from hmcpctl.server_tools.command import HMC_RUN_COMMAND_SECURITY
from hmcpctl.server_tools.inventory import capacity, composite
from hmcpctl.server_tools.lpar import (
    configuration,
    lifecycle,
    lifecycle_boot,
    lifecycle_create,
    migration,
    profiles,
    provision,
)
from hmcpctl.server_tools.metrics import pcm as metrics
from hmcpctl.server_tools.permissions import EFFECTIVE_PERMISSIONS_SECURITY
from hmcpctl.server_tools.storage import resources as storage
from hmcpctl.server_tools.systems import core as systems
from hmcpctl.server_tools.systems import health
from hmcpctl.server_tools.systems import resources as system_resources
from hmcpctl.server_tools.templates import core as templates
from hmcpctl.server_tools.users import core as users
from hmcpctl.server_tools.vios import core as vios
from hmcpctl.server_tools.vios import labels as vios_labels
from hmcpctl.server_tools.virtualization import adapters, network, pcie, vnic
from hmcpctl.tool_registry import ToolSecurity, build_tool_security

TOOL_MODULES = (
    systems,
    capacity,
    jobs,
    health,
    lifecycle,
    lifecycle_boot,
    lifecycle_create,
    vios,
    vios_labels,
    adapters,
    storage,
    network,
    pcie,
    vnic,
    migration,
    templates,
    metrics,
    users,
    updates,
    profiles,
    snapshot,
    configuration,
    system_resources,
    composite,
    provision,
    console,
)

TOOL_SECURITY: Mapping[str, ToolSecurity] = build_tool_security(
    [module.tool_security() for module in TOOL_MODULES],
    {
        "hmc_run_command": HMC_RUN_COMMAND_SECURITY,
        "hmc_effective_permissions": EFFECTIVE_PERMISSIONS_SECURITY,
    },
)
