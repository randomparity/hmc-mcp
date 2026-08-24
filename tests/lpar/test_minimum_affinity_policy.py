"""Capability-aware Power11 minimum-affinity policy reads."""

from __future__ import annotations

import asyncio
from unittest.mock import ANY, AsyncMock, patch

import pytest
from fastmcp import Client
from typer.testing import CliRunner

from hmc_mcp import cli_lpars, server_lpar_config
from hmc_mcp.access_policy import DEFAULT_CONNECTION_TOKEN
from hmc_mcp.cli import app
from hmc_mcp.config import HMCConfig
from hmc_mcp.legacy_policy import compile_legacy_policy
from hmc_mcp.operations_ssh_network import (
    MinimumAffinityPolicyResult,
    get_minimum_affinity_policy,
)
from hmc_mcp.server import TOOL_SECURITY, create_mcp
from hmc_mcp.ssh import HMCCLIError
from hmc_mcp.ssh_commands import query_minimum_affinity_policy


def _config() -> HMCConfig:
    return HMCConfig(host="hmc.test", user="u", password="p", _env_file=None)


@pytest.mark.asyncio
async def test_policy_query_uses_compatibility_gate_and_exact_projection():
    runner = AsyncMock(
        side_effect=[
            "default,POWER10,POWER11\n",
            "min_affinity_score,min_affinity_score_action\n0,none\n",
        ]
    )
    with patch("hmc_mcp.ssh_commands.run_hmc_command", runner):
        result = await query_minimum_affinity_policy(_config(), "system", "lpar one")

    assert result.min_affinity_score == 0
    assert result.min_affinity_score_action == "none"
    assert result.unavailable_reason is None
    assert runner.await_args_list[0].args[1] == (
        "lssyscfg -r sys -m system -F lpar_proc_compat_modes"
    )
    assert runner.await_args_list[1].args[1] == (
        "lssyscfg -r lpar -m system --filter 'lpar_names=lpar one' "
        "-F min_affinity_score,min_affinity_score_action --header"
    )


@pytest.mark.asyncio
async def test_policy_query_returns_capability_absence_without_policy_command():
    runner = AsyncMock(return_value="default,POWER9,POWER10\n")
    with patch("hmc_mcp.ssh_commands.run_hmc_command", runner):
        result = await query_minimum_affinity_policy(_config(), "system", "lpar")

    assert result.min_affinity_score is None
    assert result.min_affinity_score_action is None
    assert "POWER11" in result.unavailable_reason
    runner.assert_awaited_once()


@pytest.mark.parametrize(
    "output",
    [
        "",
        "min_affinity_score,min_affinity_score_action\n",
        "min_affinity_score,min_affinity_score_action\n0,none\n1,warn\n",
        "wrong,min_affinity_score_action\n0,none\n",
        "min_affinity_score,min_affinity_score_action\n,none\n",
        "min_affinity_score,min_affinity_score_action\n101,warn\n",
        "min_affinity_score,min_affinity_score_action\n1.5,warn\n",
        "min_affinity_score,min_affinity_score_action\n50,ignore\n",
    ],
)
@pytest.mark.asyncio
async def test_policy_query_rejects_malformed_output(output):
    runner = AsyncMock(side_effect=["POWER11\n", output])
    with patch("hmc_mcp.ssh_commands.run_hmc_command", runner):
        with pytest.raises(HMCCLIError, match="malformed lssyscfg minimum-affinity"):
            await query_minimum_affinity_policy(_config(), "system", "lpar")


@pytest.mark.asyncio
async def test_shared_policy_operation_resolves_names_and_wraps_result():
    query = AsyncMock(
        return_value=type(
            "Query",
            (),
            {
                "min_affinity_score": 80,
                "min_affinity_score_action": "fail",
                "unavailable_reason": None,
            },
        )()
    )
    with (
        patch(
            "hmc_mcp.operations_ssh_network.resolve_ssh_names",
            AsyncMock(return_value=("resolved-system", "resolved-lpar")),
        ),
        patch("hmc_mcp.operations_ssh_network.query_minimum_affinity_policy", query),
    ):
        result = await get_minimum_affinity_policy(_config(), "system", "lpar")

    assert result.capability == "available"
    assert result.system == "resolved-system"
    assert result.lpar == "resolved-lpar"
    assert result.min_affinity_score == 80
    assert result.min_affinity_score_action == "fail"
    assert result.unavailable_reason is None


def test_mcp_policy_adapter_delegates_to_shared_operation():
    expected = MinimumAffinityPolicyResult(
        "available", "system", "lpar", 75, "warn", None
    )
    operation = AsyncMock(return_value=expected)
    with (
        patch.object(server_lpar_config, "build_config", return_value=_config()),
        patch.object(server_lpar_config, "get_minimum_affinity_policy", operation),
    ):
        actual = server_lpar_config.hmc_get_minimum_affinity_policy(
            "system", "lpar"
        )

    assert actual == expected
    operation.assert_awaited_once_with(ANY, "system", "lpar")


def test_mcp_registers_minimum_affinity_policy_as_lpar_read():
    policy = compile_legacy_policy(TOOL_SECURITY, (DEFAULT_CONNECTION_TOKEN,))

    async def names():
        async with Client(create_mcp(policy)) as client:
            return {tool.name for tool in await client.list_tools()}

    assert "hmc_get_minimum_affinity_policy" in asyncio.run(names())
    security = TOOL_SECURITY["hmc_get_minimum_affinity_policy"]
    assert security.effect == "read"
    assert [(target.kind, target.argument) for target in security.targets] == [
        ("managed_system", "system_name_or_uuid"),
        ("lpar", "lpar_name_or_uuid"),
    ]


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            MinimumAffinityPolicyResult(
                "available", "system", "lpar", 75, "warn", None
            ),
            "minimum affinity score: 75 (warn)",
        ),
        (
            MinimumAffinityPolicyResult(
                "capability-unavailable", "system", "lpar", None, None, "upgrade"
            ),
            "unavailable: upgrade",
        ),
    ],
)
def test_cli_policy_human_output(result, expected):
    with patch.object(
        cli_lpars, "get_minimum_affinity_policy", AsyncMock(return_value=result)
    ):
        invocation = CliRunner().invoke(
            app, ["lpars", "get-minimum-affinity-policy", "lpar", "system"]
        )
    assert invocation.exit_code == 0, invocation.output
    assert expected in invocation.output


def test_cli_policy_json_delegates():
    expected = MinimumAffinityPolicyResult(
        "available", "system", "lpar", 100, "fail", None
    )
    operation = AsyncMock(return_value=expected)
    with patch.object(cli_lpars, "get_minimum_affinity_policy", operation):
        invocation = CliRunner().invoke(
            app,
            [
                "lpars",
                "get-minimum-affinity-policy",
                "lpar",
                "system",
                "--json",
            ],
        )
    assert invocation.exit_code == 0, invocation.output
    assert '"min_affinity_score": 100' in invocation.output
    operation.assert_awaited_once_with(ANY, "system", "lpar")
