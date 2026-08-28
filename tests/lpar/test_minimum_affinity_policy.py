"""Capability-aware Power11 minimum-affinity policy reads."""

from __future__ import annotations

import asyncio
from unittest.mock import ANY, AsyncMock, patch

import pytest
from fastmcp import Client
from typer.testing import CliRunner

from hmc_mcp.cli_commands.lpar import config as cli_lpars
from hmc_mcp.server_tools.lpar import configuration as server_lpar_config
from hmc_mcp.authorization.access_policy import DEFAULT_CONNECTION_TOKEN
from hmc_mcp.cli import app
from hmc_mcp.client import HMCClient
from hmc_mcp.config import HMCConfig
from hmc_mcp.cli_commands.legacy_policy import compile_legacy_policy
from hmc_mcp.operations.ssh_affinity import (
    MinimumAffinityPolicyResult,
    get_minimum_affinity_policy,
    set_minimum_affinity_policy,
)
from hmc_mcp.server import TOOL_SECURITY, create_mcp
from hmc_mcp.ssh.transport import HMCCLIError
from hmc_mcp.ssh.affinity import (
    MinimumAffinityPolicy,
    query_minimum_affinity_policy,
    set_minimum_affinity_policy_cli,
)


def _config() -> HMCConfig:
    return HMCConfig(host="hmc.test", user="u", password="p", _env_file=None)


def _client() -> HMCClient:
    return HMCClient(_config())


@pytest.mark.asyncio
async def test_policy_query_uses_compatibility_gate_and_exact_projection():
    runner = AsyncMock(
        side_effect=[
            "default,POWER10,POWER11\n",
            "min_affinity_score,min_affinity_score_action\n0,none\n",
        ]
    )
    with (
        patch("hmc_mcp.ssh.affinity.run_hmc_command", runner),
        patch("hmc_mcp.ssh.profiles.run_hmc_command", runner),
    ):
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
async def test_policy_query_accepts_quoted_compatibility_modes():
    runner = AsyncMock(
        side_effect=[
            '"default,POWER9,POWER9_base,POWER10,POWER11"\n',
            "min_affinity_score,min_affinity_score_action\n0,none\n",
        ]
    )
    with (
        patch("hmc_mcp.ssh.affinity.run_hmc_command", runner),
        patch("hmc_mcp.ssh.profiles.run_hmc_command", runner),
    ):
        result = await query_minimum_affinity_policy(_config(), "system", "lpar")

    assert result.min_affinity_score == 0
    assert result.min_affinity_score_action == "none"
    assert runner.await_count == 2


@pytest.mark.asyncio
async def test_policy_query_returns_capability_absence_without_policy_command():
    runner = AsyncMock(return_value="default,POWER9,POWER10\n")
    with (
        patch("hmc_mcp.ssh.affinity.run_hmc_command", runner),
        patch("hmc_mcp.ssh.profiles.run_hmc_command", runner),
    ):
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
    with (
        patch("hmc_mcp.ssh.affinity.run_hmc_command", runner),
        patch("hmc_mcp.ssh.profiles.run_hmc_command", runner),
    ):
        with pytest.raises(HMCCLIError, match="malformed lssyscfg minimum-affinity"):
            await query_minimum_affinity_policy(_config(), "system", "lpar")


@pytest.mark.parametrize("score", [-1, 101, True])
@pytest.mark.asyncio
async def test_policy_setter_rejects_invalid_score_before_hmc_call(score):
    runner = AsyncMock()
    policy = MinimumAffinityPolicy(score, "warn")
    with (
        patch("hmc_mcp.ssh.affinity.run_hmc_command", runner),
        patch("hmc_mcp.ssh.profiles.run_hmc_command", runner),
    ):
        with pytest.raises(ValueError, match="integer from 0 through 100"):
            await set_minimum_affinity_policy_cli(_config(), "system", "lpar", policy)
    runner.assert_not_awaited()


@pytest.mark.asyncio
async def test_policy_setter_rejects_unsupported_system_before_mutation():
    runner = AsyncMock(return_value="default,POWER10\n")
    with (
        patch("hmc_mcp.ssh.affinity.run_hmc_command", runner),
        patch("hmc_mcp.ssh.profiles.run_hmc_command", runner),
    ):
        with pytest.raises(HMCCLIError, match="advertises POWER11"):
            await set_minimum_affinity_policy_cli(
                _config(), "system", "lpar", MinimumAffinityPolicy(80, "warn")
            )
    runner.assert_awaited_once()
    assert runner.await_args.args[1].startswith("lssyscfg -r sys")


@pytest.mark.asyncio
async def test_policy_setter_requires_deliberate_fail_and_quotes_command():
    runner = AsyncMock(side_effect=["POWER11\n", "changed\n"])
    with (
        patch("hmc_mcp.ssh.affinity.run_hmc_command", runner),
        patch("hmc_mcp.ssh.profiles.run_hmc_command", runner),
    ):
        result = await set_minimum_affinity_policy_cli(
            _config(), "system one", "lpar one", MinimumAffinityPolicy(90, "fail")
        )
    assert result == "changed\n"
    assert runner.await_args_list[1].args[1] == (
        "chsyscfg -r lpar -m 'system one' "
        "-i 'name=lpar one,min_affinity_score=90,min_affinity_score_action=fail'"
    )


@pytest.mark.asyncio
async def test_public_policy_setter_validates_before_resolution():
    hmc = AsyncMock()
    hmc.config = _config()
    resolver = AsyncMock()
    with patch(
                "hmc_mcp.operations.ssh_affinity.resolve_and_authorize_lpar_names",
        resolver,
    ):
        with pytest.raises(ValueError, match="none, warn, or fail"):
            await set_minimum_affinity_policy(
                hmc,
                "system",
                "lpar",
                MinimumAffinityPolicy(80, "invalid"),  # type: ignore[arg-type]
            )
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_policy_setter_authorizes_before_mutation():
    hmc = AsyncMock()
    hmc.config = _config()
    events: list[str] = []
    authorize = AsyncMock(
        side_effect=lambda *args, **kwargs: (
            events.append("authorize") or ("system", "lpar")
        )
    )
    mutate = AsyncMock(side_effect=lambda *args: events.append("mutate") or "changed")
    with (
        patch(
                "hmc_mcp.operations.ssh_affinity.resolve_and_authorize_lpar_names",
            authorize,
        ),
            patch("hmc_mcp.operations.ssh_affinity.set_minimum_affinity_policy_cli", mutate),
    ):
        result = await set_minimum_affinity_policy(
            hmc, "system", "lpar", MinimumAffinityPolicy(80, "warn")
        )
    assert result == "changed"
    assert events == ["authorize", "mutate"]


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
                "hmc_mcp.operations.ssh_affinity.resolve_ssh_names",
            AsyncMock(return_value=("resolved-system", "resolved-lpar")),
        ),
            patch("hmc_mcp.operations.ssh_affinity.query_minimum_affinity_policy", query),
    ):
        result = await get_minimum_affinity_policy(_client(), "system", "lpar")

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
    client = _client()
    context = AsyncMock()
    context.__aenter__.return_value = client
    with (
        patch.object(server_lpar_config, "client_from_env", return_value=context),
        patch.object(server_lpar_config, "get_minimum_affinity_policy", operation),
    ):
        actual = server_lpar_config.hmc_get_minimum_affinity_policy("system", "lpar")

    assert actual == expected
    operation.assert_awaited_once_with(client, "system", "lpar")


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
    with (
        patch.object(cli_lpars, "_ssh_client", return_value=_client()),
        patch.object(
            cli_lpars, "get_minimum_affinity_policy", AsyncMock(return_value=result)
        ),
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
    with (
        patch.object(cli_lpars, "_ssh_client", return_value=_client()),
        patch.object(cli_lpars, "get_minimum_affinity_policy", operation),
    ):
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
