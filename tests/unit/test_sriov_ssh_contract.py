from unittest.mock import AsyncMock

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.ssh_network import (
    assign_sriov_logical_port_dynamic,
    list_sriov_adapter_rows,
    list_sriov_physical_port_rows,
    unassign_sriov_logical_port_profile,
)


def _config():
    return HMCConfig(host="h", user="u", password="p", _env_file=None)


@pytest.mark.asyncio
async def test_exact_sriov_read_and_mutation_commands(monkeypatch):
    run = AsyncMock(
        side_effect=[
            "adapter_id,slot_id,config_state,functional_state,phys_loc,phys_ports,logical_ports,adapter_max_logical_ports,sriov_status\n1,2,sriov,1,U,2,120,120,running\n",
            "adapter_id,phys_port_id,phys_port_type,phys_port_loc,state,config_logical_ports,phys_port_max_logical_ports,curr_eth_logical_ports\n1,0,roce,U-T1,1,0,60,0\n",
            "",
            "",
        ]
    )
    monkeypatch.setattr("hmc_mcp.ssh_network.run_hmc_command", run)
    assert (await list_sriov_adapter_rows(_config(), "sys"))[0][
        "config_state"
    ] == "sriov"
    assert (await list_sriov_physical_port_rows(_config(), "sys", "1"))[0][
        "phys_port_type"
    ] == "roce"
    await assign_sriov_logical_port_dynamic(
        _config(), "sys", "lpar", "1", "0", "3", "2.0"
    )
    await unassign_sriov_logical_port_profile(_config(), "sys", "lpar", "prof")
    commands = [call.args[1] for call in run.await_args_list]
    assert "--level roce" in commands[1]
    assert "--rsubtype logport" in commands[2] and "-o a" in commands[2]
    assert "sriov_eth_logical_ports=none" in commands[3]
    assert all("--force" not in command for command in commands)
