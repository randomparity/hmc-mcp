"""Regression tests for MCP handler and SSH transport ownership."""

from __future__ import annotations

import hmc_mcp.server as server
import hmc_mcp.ssh as ssh


def test_domain_handlers_live_in_focused_modules() -> None:
    expected_modules = {
        server.hmc_list_adapters: "hmc_mcp.server_adapters",
        server.hmc_get_job: "hmc_mcp.server_jobs",
        server.hmc_capacity_report: "hmc_mcp.server_capacity",
        server.hmc_run_command: "hmc_mcp.server_command",
        server.hmc_modify_system: "hmc_mcp.server_systems",
        server.hmc_power_on_system: "hmc_mcp.server_systems",
        server.hmc_power_off_system: "hmc_mcp.server_systems",
        server.hmc_power_on_vios: "hmc_mcp.server_vios",
        server.hmc_power_off_vios: "hmc_mcp.server_vios",
        server.hmc_power_on_lpar: "hmc_mcp.server_lpars",
        server.hmc_power_off_lpar: "hmc_mcp.server_lpars",
        server.hmc_get_lpar_description: "hmc_mcp.server_lpar_config",
        server.hmc_get_proc_compat_modes: "hmc_mcp.server_system_resources",
        server.hmc_list_memory_pools: "hmc_mcp.server_system_resources",
    }

    for handler, module_name in expected_modules.items():
        assert handler.__module__ == module_name


def test_ssh_transport_does_not_own_resource_commands() -> None:
    transport_api = {"HMCCLIError", "run_hmc_command", "run_hmc_cli"}
    public_names = {name for name in vars(ssh) if not name.startswith("_")}

    assert transport_api <= public_names
    assert "get_lpar_description" not in public_names
    assert "list_memory_pools" not in public_names
