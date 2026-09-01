import json
import shlex
from pathlib import Path
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
from hmc_mcp.ssh.transport import HMCCLIError


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

_EVIDENCE_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "sriov"
    / "sriov-physport-selection-v10r3-v11r2.json"
)


def _selection_cases() -> list[dict[str, object]]:
    return json.loads(_EVIDENCE_PATH.read_text())["selection_cases"]


def test_physical_port_evidence_preserves_live_verification_contract():
    evidence = json.loads(_EVIDENCE_PATH.read_text())

    assert evidence["survey_scope"]["hmc_releases"] == [
        {"release": "V10R3 M1060", "build": "2408210051"},
        {"release": "V11R2 SP1120", "build": "2607082225"},
    ]
    assert evidence["survey_scope"]["machine_types"] == [
        {"model": "8375-42A", "ethc": True, "roce": True},
        {"model": "9009-42G", "ethc": False, "roce": True},
        {"model": "9009-42A", "ethc": True, "roce": False},
        {"model": "9009-22G", "ethc": False, "roce": True},
        {"model": "9040-MR9", "ethc": False, "roce": True},
        {"model": "9043-MRX", "ethc": False, "roce": True},
        {"model": "9043-MRU", "ethc": False, "roce": False},
        {"model": "9080-HEU", "ethc": False, "roce": True},
        {"model": "9080-HEX", "ethc": False, "roce": True},
        {"model": "9080-M9S", "ethc": False, "roce": True},
        {"model": "9105-22A", "ethc": False, "roce": True},
        {"model": "9119-MHE", "ethc": True, "roce": False},
        {"model": "9028-21B", "ethc": False, "roce": True},
    ]
    assert [
        (case["hmc_release"], case["hmc_build"], case["system_model"])
        for case in evidence["selection_cases"]
    ] == [
        ("V10R3 M1060", "2408210051", "8375-42A"),
        ("V11R2", "not captured for this system", "8375-42A"),
    ]
    assert {
        (case["roce"]["exit_status"], case["ethc"]["exit_status"])
        for case in evidence["selection_cases"]
    } == {(0, 0)}
    malformed_filters = [
        error
        for error in evidence["error_cases"]
        if error["result_class"] == "malformed-filter"
    ]
    assert {
        error["adapter_id"]: (error["exit_status"], error["stderr"])
        for error in malformed_filters
    } == {"null": (1, ""), "unavailable": (1, "")}
    assert all(
        'invalid value is "adapter_ids"' in error["stdout"]
        for error in malformed_filters
    )
    unsupported = next(
        error
        for error in evidence["error_cases"]
        if error["result_class"] == "unsupported-system-state"
    )
    assert unsupported == {
        "result_class": "unsupported-system-state",
        "adapter_id": "not applicable",
        "hmc_releases": ["not captured for these systems"],
        "exit_status": 1,
        "stdout": (
            "HSCL9010 This operation is only allowed when the managed system is in "
            "the Standby or Operating state.\n"
        ),
        "stderr": "",
    }
    assert evidence["observations"] == {
        "empty_result": "No results were found.",
        "empty_result_exit_status": 0,
        "state_values": {"0": "down", "1": "up"},
        "both_levels_returned_rows": False,
        "ambiguity_observed": False,
        "mutual_exclusion": (
            "Every numeric adapter_id returned rows at exactly one level across the "
            "survey."
        ),
        "applicability": (
            "The same selection behavior was observed on V10R3 M1060 and V11R2 "
            "SP1120 across the surveyed Power8, Power9, and Power10 machine types; "
            "no version or model exception was found."
        ),
    }


def _physical_port_output(adapter_id: str = "1", port_type: str = "roce") -> str:
    return (
        f"{','.join(_PHYSICAL_FIELDS)}\n"
        f"{adapter_id},0,{port_type},U-T1,1,0,60,0\n"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    _selection_cases(),
    ids=lambda case: case["name"],
)
async def test_physical_port_selects_the_sole_populated_level(
    monkeypatch, case
):
    run = AsyncMock(side_effect=[case["roce"]["stdout"], case["ethc"]["stdout"]])
    monkeypatch.setattr("hmc_mcp.ssh.network.run_hmc_command", run)

    assert await list_sriov_physical_port_rows(
        _config(), "sys", case["adapter_id"]
    ) == case["expected_rows"]
    commands = [call.args[1] for call in run.await_args_list]
    assert len(commands) == 2
    expected_filter = (
        f"--filter "
        f"{shlex.quote(build_filter([('adapter_ids', case['adapter_id'])]))}"
    )
    expected_projection = f"-F {','.join(_PHYSICAL_FIELDS)} --header"
    assert all(
        expected_filter in command and expected_projection in command
        for command in commands
    )
    assert "--level roce" in commands[0]
    assert "--level ethc" in commands[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter_id", ["0", "-1", "null", "unavailable", "\u0661", "\uff11"]
)
async def test_physical_port_rejects_invalid_adapter_ids_without_ssh(
    monkeypatch, adapter_id
):
    run = AsyncMock()
    monkeypatch.setattr("hmc_mcp.ssh.network.run_hmc_command", run)

    with pytest.raises(ValueError, match="adapter_id"):
        await list_sriov_physical_port_rows(_config(), "sys", adapter_id)

    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_physical_port_propagates_first_command_error_without_second_read(monkeypatch):
    error = HMCCLIError("RoCE read refused")
    run = AsyncMock(side_effect=error)
    monkeypatch.setattr("hmc_mcp.ssh.network.run_hmc_command", run)

    with pytest.raises(HMCCLIError, match="RoCE read refused") as caught:
        await list_sriov_physical_port_rows(_config(), "sys", "1")

    assert caught.value is error
    assert run.await_count == 1
    assert "--level roce" in run.await_args_list[0].args[1]


@pytest.mark.asyncio
async def test_physical_port_propagates_second_command_error_before_parsing(monkeypatch):
    error = HMCCLIError("ethc read refused")
    run = AsyncMock(side_effect=["wrong,header\n1,2\n", error])
    monkeypatch.setattr("hmc_mcp.ssh.network.run_hmc_command", run)

    with pytest.raises(HMCCLIError, match="ethc read refused") as caught:
        await list_sriov_physical_port_rows(_config(), "sys", "1")

    assert caught.value is error
    assert run.await_count == 2
    assert [
        "--level roce" in run.await_args_list[0].args[1],
        "--level ethc" in run.await_args_list[1].args[1],
    ] == [
        True,
        True,
    ]


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
@pytest.mark.parametrize(
    ("roce_output", "ethc_output"),
    [
        ("wrong,header\n1,2\n", "No results were found."),
        ("No results were found.", "wrong,header\n1,2\n"),
    ],
    ids=("malformed-roce", "malformed-ethc"),
)
async def test_physical_port_reads_both_levels_before_rejecting_malformed_output(
    monkeypatch, roce_output, ethc_output
):
    run = AsyncMock(side_effect=[roce_output, ethc_output])
    monkeypatch.setattr("hmc_mcp.ssh.network.run_hmc_command", run)

    with pytest.raises(ValueError, match="header does not match"):
        await list_sriov_physical_port_rows(_config(), "sys", "1")

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
