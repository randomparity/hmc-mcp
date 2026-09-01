import shlex
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.ssh.commands import build_filter
from hmc_mcp.ssh.network import (
    assign_sriov_logical_port_dynamic,
    list_sriov_adapter_rows,
    list_sriov_physical_port_rows,
    unassign_sriov_logical_port_profile,
)


def _config():
    return HMCConfig(host="h", user="u", password="p")


_PHYSICAL_FIELDS = (
    "adapter_id",
    "phys_port_id",
    "phys_port_type",
    "phys_port_loc",
    "state",
    "config_logical_ports",
    "phys_port_max_logical_ports",
    "curr_eth_logical_ports",
)


def _physical_port_output(adapter_id: str = "1", port_type: str = "roce") -> str:
    return (
        f"{','.join(_PHYSICAL_FIELDS)}\n"
        f"{adapter_id},0,{port_type},U-T1,1,0,60,0\n"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("roce_output", "ethc_output", "expected_type"),
    [
        (_physical_port_output(port_type="eth"), "No results were found.", "eth"),
        ("No results were found.", _physical_port_output(port_type="ethc"), "ethc"),
    ],
)
async def test_physical_port_selects_the_sole_populated_level(
    monkeypatch, roce_output, ethc_output, expected_type
):
    run = AsyncMock(side_effect=[roce_output, ethc_output])
    monkeypatch.setattr("hmc_mcp.ssh.network.run_hmc_command", run)

    assert await list_sriov_physical_port_rows(_config(), "sys", "1") == [
        {
            "adapter_id": "1",
            "phys_port_id": "0",
            "phys_port_type": expected_type,
            "phys_port_loc": "U-T1",
            "state": "1",
            "config_logical_ports": "0",
            "phys_port_max_logical_ports": "60",
            "curr_eth_logical_ports": "0",
        }
    ]
    commands = [call.args[1] for call in run.await_args_list]
    assert len(commands) == 2
    expected_filter = f"--filter {shlex.quote(build_filter([('adapter_ids', '1')]))}"
    expected_projection = f"-F {','.join(_PHYSICAL_FIELDS)} --header"
    assert all(
        expected_filter in command and expected_projection in command
        for command in commands
    )
    assert "--level roce" in commands[0]
    assert "--level ethc" in commands[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", ["0", "-1", "null", "unavailable"])
async def test_physical_port_rejects_invalid_adapter_ids_without_ssh(
    monkeypatch, adapter_id
):
    run = AsyncMock()
    monkeypatch.setattr("hmc_mcp.ssh.network.run_hmc_command", run)

    with pytest.raises(ValueError, match="adapter_id"):
        await list_sriov_physical_port_rows(_config(), "sys", adapter_id)

    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_physical_port_rejects_ambiguous_or_mismatched_rows(monkeypatch):
    run = AsyncMock(
        side_effect=[
            _physical_port_output(),
            _physical_port_output(port_type="ethc"),
        ]
    )
    monkeypatch.setattr("hmc_mcp.ssh.network.run_hmc_command", run)

    with pytest.raises(ValueError, match="both roce and ethc"):
        await list_sriov_physical_port_rows(_config(), "sys", "1")

    run = AsyncMock(
        side_effect=[_physical_port_output(adapter_id="2"), "No results were found."]
    )
    monkeypatch.setattr("hmc_mcp.ssh.network.run_hmc_command", run)
    with pytest.raises(ValueError, match="adapter_id"):
        await list_sriov_physical_port_rows(_config(), "sys", "1")


@pytest.mark.asyncio
async def test_physical_port_returns_empty_when_both_levels_are_empty(monkeypatch):
    run = AsyncMock(
        side_effect=["No results were found.", "No results were found."]
    )
    monkeypatch.setattr("hmc_mcp.ssh.network.run_hmc_command", run)

    assert await list_sriov_physical_port_rows(_config(), "sys", "1") == []
    assert run.await_count == 2


@pytest.mark.asyncio
async def test_exact_sriov_read_and_mutation_commands(monkeypatch):
    run = AsyncMock(
        side_effect=[
            "adapter_id,slot_id,config_state,functional_state,phys_loc,phys_ports,logical_ports,adapter_max_logical_ports,sriov_status\n1,2,sriov,1,U,2,120,120,running\n",
            "adapter_id,phys_port_id,phys_port_type,phys_port_loc,state,config_logical_ports,phys_port_max_logical_ports,curr_eth_logical_ports\n1,0,roce,U-T1,1,0,60,0\n",
            "No results were found.",
            "",
            "",
        ]
    )
    monkeypatch.setattr("hmc_mcp.ssh.network.run_hmc_command", run)
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
    assert "--level ethc" in commands[2]
    assert "--rsubtype logport" in commands[3] and "-o a" in commands[3]
    assert "sriov_eth_logical_ports=none" in commands[4]
    assert all("--force" not in command for command in commands)
