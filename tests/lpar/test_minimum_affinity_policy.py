"""Capability-aware Power11 minimum-affinity policy reads."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from hmc_mcp.config import HMCConfig
from hmc_mcp.operations_ssh_network import get_minimum_affinity_policy
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

