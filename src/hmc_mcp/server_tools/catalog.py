"""Immutable security catalog for every MCP tool shipped by this package."""

from __future__ import annotations

from collections.abc import Mapping

from hmc_mcp.server_tools import (
    adapters,
    capacity,
    composite,
    console,
    health,
    jobs,
    lpm,
    metrics,
    network,
    pcie,
    snapshot,
    storage,
    system_resources,
    systems,
    templates,
    updates,
    users,
    vios,
    vios_labels,
    vnic,
)
from hmc_mcp.server_tools.lpar import configuration, lifecycle, profiles, provision
from hmc_mcp.server_tools.command import HMC_RUN_COMMAND_SECURITY
from hmc_mcp.server_tools.permissions import EFFECTIVE_PERMISSIONS_SECURITY
from hmc_mcp.tool_registry import ToolSecurity, build_tool_security


TOOL_MODULES = (
    systems,
    capacity,
    jobs,
    health,
    lifecycle,
    vios,
    vios_labels,
    adapters,
    storage,
    network,
    pcie,
    vnic,
    lpm,
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
