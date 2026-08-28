"""Capability-aware resource-group affinity queries."""

from __future__ import annotations

import asyncio
from unittest.mock import ANY, AsyncMock, patch

import pytest
from fastmcp import Client
from typer.testing import CliRunner

from hmc_mcp.cli_commands.lpar import config as cli_lpars
from hmc_mcp.server_tools.lpar import configuration as server_lpar_config
from hmc_mcp.authorization.access_policy import DEFAULT_CONNECTION_TOKEN
from hmc_mcp.client import HMCClient
from hmc_mcp.config import HMCConfig
from hmc_mcp.cli_commands.legacy_policy import compile_legacy_policy
from hmc_mcp.operations.ssh_affinity import (
    ResourceGroupAffinityResult,
    list_resource_group_memopt_scores,
)
from hmc_mcp.server import TOOL_SECURITY, create_mcp
from hmc_mcp.cli import app
from hmc_mcp.ssh.transport import HMCCLIError
from hmc_mcp.ssh.affinity import (
    MemoptResourceGroupSelector,
    query_resource_group_memopt_scores,
)


def _config() -> HMCConfig:
    return HMCConfig(host="hmc.test", user="u", password="p", _env_file=None)


def _client() -> HMCClient:
    return HMCClient(_config())


V11 = "version= Version: 11\n Release: 2\n Service Pack: 1120\n"
CURRENT = (
    "resource_group_name,resource_group_id,curr_score\nDefault Resource Group,0,100\n"
)
CALCULATED = (
    "resource_group_name,resource_group_id,curr_score,predicted_score,"
    "requested_lpar_names,requested_lpar_ids,protected_lpar_names,protected_lpar_ids\n"
    "Default Resource Group,0,none,100,,none,,none\n"
)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"names": ("",)},
        {"names": ("a", "a")},
        {"ids": (-1,)},
        {"ids": (1, 1)},
        {"names": ("a",), "ids": (0,)},
        {"names": ("a",), "all": True},
    ],
)
def test_resource_group_selector_rejects_invalid_modes(kwargs):
    with pytest.raises(ValueError):
        MemoptResourceGroupSelector(**kwargs)


def test_resource_group_selector_accepts_default_group_id_zero():
    assert MemoptResourceGroupSelector(ids=(0,)).ids == (0,)


@pytest.mark.parametrize(
    ("selector", "fragment"),
    [
        (MemoptResourceGroupSelector(all=True), "--gid all"),
        (MemoptResourceGroupSelector(ids=(0, 2)), "--gid 0,2"),
        (
            MemoptResourceGroupSelector(names=("Default Resource Group",)),
            "-g 'Default Resource Group'",
        ),
    ],
)
def test_current_query_uses_exact_projection_and_selector(selector, fragment):
    commands: list[str] = []

    async def run(_config, command):
        commands.append(command)
        return V11 if command == "lshmc -V" else CURRENT

    with patch("hmc_mcp.ssh.affinity.run_hmc_command", run):
        result = asyncio.run(
            query_resource_group_memopt_scores(
                _config(), "system", selector, calculated=False
            )
        )

    assert result.unavailable_reason is None
    assert result.items == [
        {
            "resource_group_name": "Default Resource Group",
            "resource_group_id": "0",
            "curr_score": "100",
        }
    ]
    assert fragment in commands[1]
    assert "-F resource_group_name,resource_group_id,curr_score --header" in commands[1]


def test_calculated_query_preserves_sentinel_and_marks_prediction():
    runner = AsyncMock(side_effect=[V11, CALCULATED])
    with patch("hmc_mcp.ssh.affinity.run_hmc_command", runner):
        result = asyncio.run(
            query_resource_group_memopt_scores(
                _config(),
                "system",
                MemoptResourceGroupSelector(all=True),
                calculated=True,
            )
        )
    assert result.items[0]["curr_score"] == "none"
    assert result.items[0]["prediction_guaranteed"] is False


@pytest.mark.parametrize("version", ["", "Version: eleven", "V10R3M1060"])
def test_unadmitted_hmc_returns_capability_without_score_query(version):
    runner = AsyncMock(return_value=version)
    with patch("hmc_mcp.ssh.affinity.run_hmc_command", runner):
        result = asyncio.run(
            query_resource_group_memopt_scores(
                _config(),
                "system",
                MemoptResourceGroupSelector(all=True),
                calculated=False,
            )
        )
    assert result.items == []
    assert "HMC V11R1M1110 or later" in result.unavailable_reason
    runner.assert_awaited_once_with(_config(), "lshmc -V")


def test_hsclca00_returns_managed_system_capability_result():
    runner = AsyncMock(side_effect=[V11, HMCCLIError("HSCLCA00 unsupported")])
    with patch("hmc_mcp.ssh.affinity.run_hmc_command", runner):
        result = asyncio.run(
            query_resource_group_memopt_scores(
                _config(),
                "system",
                MemoptResourceGroupSelector(all=True),
                calculated=False,
            )
        )
    assert result.items == []
    assert "managed system" in result.unavailable_reason


def test_noncapability_failure_propagates():
    runner = AsyncMock(side_effect=[V11, HMCCLIError("permission denied")])
    with patch("hmc_mcp.ssh.affinity.run_hmc_command", runner):
        with pytest.raises(HMCCLIError, match="permission denied"):
            asyncio.run(
                query_resource_group_memopt_scores(
                    _config(),
                    "system",
                    MemoptResourceGroupSelector(all=True),
                    calculated=False,
                )
            )


def test_error_that_only_mentions_hsclca00_propagates():
    runner = AsyncMock(
        side_effect=[V11, HMCCLIError("diagnostic says HSCLCA00 was not returned")]
    )
    with patch("hmc_mcp.ssh.affinity.run_hmc_command", runner):
        with pytest.raises(HMCCLIError, match="was not returned"):
            asyncio.run(
                query_resource_group_memopt_scores(
                    _config(),
                    "system",
                    MemoptResourceGroupSelector(all=True),
                    calculated=False,
                )
            )


@pytest.mark.parametrize(
    "output", ["", " \n", "resource_group_name,resource_group_id,curr_score\n"]
)
def test_blank_output_fails_but_header_only_is_empty(output):
    runner = AsyncMock(side_effect=[V11, output])
    with patch("hmc_mcp.ssh.affinity.run_hmc_command", runner):
        call = query_resource_group_memopt_scores(
            _config(), "system", MemoptResourceGroupSelector(all=True), calculated=False
        )
        if output.strip():
            assert asyncio.run(call).items == []
        else:
            with pytest.raises(HMCCLIError, match="missing its header"):
                asyncio.run(call)


@pytest.mark.parametrize(
    "output",
    [
        "resource_group_id,resource_group_name,curr_score\n0,Default,100\n",
        "resource_group_name,resource_group_id,curr_score\nDefault,0\n",
        "resource_group_name,resource_group_id,curr_score\nDefault,,100\n",
    ],
)
def test_malformed_resource_group_output_is_actionable(output):
    runner = AsyncMock(side_effect=[V11, output])
    with patch("hmc_mcp.ssh.affinity.run_hmc_command", runner):
        with pytest.raises(HMCCLIError):
            asyncio.run(
                query_resource_group_memopt_scores(
                    _config(),
                    "system",
                    MemoptResourceGroupSelector(all=True),
                    calculated=False,
                )
            )


def test_shared_operation_resolves_system_and_defaults_to_all():
    query = AsyncMock(
        return_value=type("Query", (), {"items": [], "unavailable_reason": None})()
    )
    with (
        patch(
                "hmc_mcp.operations.ssh_affinity.resolve_ssh_names",
            AsyncMock(return_value=("resolved-system", None)),
        ) as resolve,
        patch(
                "hmc_mcp.operations.ssh_affinity.query_resource_group_memopt_scores", query
        ),
    ):
        result = asyncio.run(
            list_resource_group_memopt_scores(_client(), "system-uuid")
        )
    assert result.capability == "available"
    assert result.selector == MemoptResourceGroupSelector(all=True)
    resolve.assert_awaited_once_with(_config(), "system-uuid", None)
    query.assert_awaited_once_with(
        _config(),
        "resolved-system",
        MemoptResourceGroupSelector(all=True),
        calculated=False,
    )


def test_mcp_adapter_delegates_to_shared_operation():
    expected = ResourceGroupAffinityResult(
        "capability-unavailable",
        "current",
        "system",
        MemoptResourceGroupSelector(all=True),
        [],
        "upgrade HMC",
    )
    operation = AsyncMock(return_value=expected)
    client = HMCClient(_config())
    context = AsyncMock()
    context.__aenter__.return_value = client
    with (
        patch.object(server_lpar_config, "client_from_env", return_value=context),
        patch.object(
            server_lpar_config, "list_resource_group_memopt_scores", operation
        ),
    ):
        actual = server_lpar_config.hmc_list_resource_group_memopt_scores("system")
    assert actual == expected
    operation.assert_awaited_once_with(client, "system", None)


def test_mcp_registers_both_resource_group_affinity_tools():
    policy = compile_legacy_policy(TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,))

    async def names():
        async with Client(create_mcp(policy)) as client:
            return {tool.name for tool in await client.list_tools()}

    assert {
        "hmc_list_resource_group_memopt_scores",
        "hmc_plan_resource_group_memopt_scores",
    } <= asyncio.run(names())


def test_resource_group_cli_delegates_id_zero_and_prints_json():
    expected = ResourceGroupAffinityResult(
        "available",
        "current",
        "system",
        MemoptResourceGroupSelector(ids=(0,)),
        [],
        None,
    )
    operation = AsyncMock(return_value=expected)
    with (
        patch.object(cli_lpars, "_ssh_client", return_value=_client()),
        patch.object(cli_lpars, "list_resource_group_memopt_scores", operation),
    ):
        result = CliRunner().invoke(
            app,
            [
                "lpars",
                "resource-group-memopt-scores",
                "system",
                "--resource-group-id",
                "0",
                "--json",
            ],
        )
    assert result.exit_code == 0, result.output
    assert '"capability": "available"' in result.output
    operation.assert_awaited_once_with(
        ANY, "system", MemoptResourceGroupSelector(ids=(0,))
    )


def test_resource_group_cli_rejects_mixed_selectors():
    result = CliRunner().invoke(
        app,
        [
            "lpars",
            "resource-group-memopt-scores",
            "system",
            "--resource-group-name",
            "Default Resource Group",
            "--all",
        ],
    )
    assert result.exit_code == 2
