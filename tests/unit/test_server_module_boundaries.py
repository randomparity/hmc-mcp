"""Regression tests for MCP handler and SSH transport ownership."""

from __future__ import annotations

import ast
from pathlib import Path

import hmc_mcp.ssh.transport as ssh
from hmc_mcp import server
from hmc_mcp.cli_commands import network as cli_network
from hmc_mcp.cli_commands import pcie as cli_pcie
from hmc_mcp.cli_commands import vnic as cli_vnic
from hmc_mcp.server_tools import (
    adapters,
    capacity,
    command,
    health,
    jobs,
    network,
    pcie,
    storage,
    system_resources,
    systems,
    vios,
    vnic,
)
from hmc_mcp.server_tools.lpar import configuration, lifecycle


def test_domain_handlers_live_in_focused_modules() -> None:
    expected_modules = {
        health.hmc_fleet_health: "hmc_mcp.server_tools.health",
        storage.hmc_attach_disk_to_lpar: "hmc_mcp.server_tools.storage",
        adapters.hmc_list_adapters: "hmc_mcp.server_tools.adapters",
        jobs.hmc_get_job: "hmc_mcp.server_tools.jobs",
        capacity.hmc_capacity_report: "hmc_mcp.server_tools.capacity",
        command.hmc_run_command: "hmc_mcp.server_tools.command",
        systems.hmc_modify_system: "hmc_mcp.server_tools.systems",
        systems.hmc_power_on_system: "hmc_mcp.server_tools.systems",
        systems.hmc_power_off_system: "hmc_mcp.server_tools.systems",
        vios.hmc_power_on_vios: "hmc_mcp.server_tools.vios",
        vios.hmc_power_off_vios: "hmc_mcp.server_tools.vios",
        lifecycle.hmc_power_on_lpar: "hmc_mcp.server_tools.lpar.lifecycle",
        lifecycle.hmc_power_off_lpar: "hmc_mcp.server_tools.lpar.lifecycle",
        configuration.hmc_get_lpar_description: (
            "hmc_mcp.server_tools.lpar.configuration"
        ),
        system_resources.hmc_get_proc_compat_modes: (
            "hmc_mcp.server_tools.system_resources"
        ),
        system_resources.hmc_list_memory_pools: (
            "hmc_mcp.server_tools.system_resources"
        ),
        network.hmc_list_virtual_networks: "hmc_mcp.server_tools.network",
        pcie.hmc_set_sriov_adapter_mode: "hmc_mcp.server_tools.pcie",
        vnic.hmc_list_vnics: "hmc_mcp.server_tools.vnic",
    }

    for handler, module_name in expected_modules.items():
        assert handler.__module__ == module_name


def test_network_cli_commands_follow_operation_domains() -> None:
    expected_modules = {
        cli_network.network_list_networks: "hmc_mcp.cli_commands.network",
        cli_pcie.network_list_sriov_adapters: "hmc_mcp.cli_commands.pcie",
        cli_vnic.network_list_vnics: "hmc_mcp.cli_commands.vnic",
    }

    for handler, module_name in expected_modules.items():
        assert handler.__module__ == module_name


def test_ssh_transport_does_not_own_resource_commands() -> None:
    transport_api = {"HMCCLIError", "run_hmc_command", "run_hmc_cli"}
    public_names = {name for name in vars(ssh) if not name.startswith("_")}

    assert transport_api <= public_names
    assert "get_lpar_description" not in public_names
    assert "list_memory_pools" not in public_names


def test_server_tools_do_not_construct_unmanaged_hmc_clients() -> None:
    server_tools = Path(server.__file__).parent / "server_tools"
    direct_constructors: list[str] = []
    for path in server_tools.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "HMCClient":
                direct_constructors.append(f"{path.name}:{node.lineno}")

    assert direct_constructors == []
