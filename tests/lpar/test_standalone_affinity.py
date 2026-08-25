"""Standalone PowerOn affinity-assessment contract tests."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import AsyncMock, patch

import pytest

from hmc_mcp.client import HMCClient
from hmc_mcp.operations_lpar import (
    LparPowerResult,
    ProvisionAffinityAssessment,
    assess_post_activation_affinity,
    activation_allows_assessment,
    classify_affinity_outcome,
)


def _request(response: Literal["warn", "fail"] = "warn") -> ProvisionAffinityAssessment:
    return ProvisionAffinityAssessment(
        system_name_or_uuid="system-1",
        lpar_name="lpar-1",
        captured_score=80,
        captured_policy_state="absent",
        captured_minimum=None,
        captured_at=datetime.now(UTC),
        stale_after_seconds=300,
        response=response,
        regression_threshold=5,
        optimization_threshold=5,
    )


def _assessment(classification: str, explanation: str = "reason") -> dict:
    return {
        "assessment": {
            "classification": classification,
            "explanation": explanation,
        }
    }


def test_unsupported_measurement_is_unavailable_for_warning_intent() -> None:
    outcome = classify_affinity_outcome(_assessment("unsupported-data"), "warn")

    assert outcome.measured is True
    assert outcome.status == "unavailable"
    assert outcome.reason == "reason"


def test_adverse_measurement_warns_for_warning_intent() -> None:
    outcome = classify_affinity_outcome(_assessment("regression"), "warn")

    assert outcome.measured is True
    assert outcome.status == "warned"


def test_adverse_measurement_fails_closed() -> None:
    outcome = classify_affinity_outcome(_assessment("policy-violation"), "fail")

    assert outcome.measured is True
    assert outcome.status == "failed"


def test_unsupported_measurement_fails_closed() -> None:
    outcome = classify_affinity_outcome(_assessment("unsupported-data"), "fail")

    assert outcome.measured is True
    assert outcome.status == "failed"


def test_timeout_does_not_allow_measurement() -> None:
    allowed, reason = activation_allows_assessment(
        LparPowerResult("lpar-1", {"Resource": {"JobID": "job-1", "Status": "RUNNING"}})
    )

    assert allowed is False
    assert "timeout" in reason.lower()


@pytest.mark.asyncio
async def test_measurement_runs_current_prediction_and_policy_reads_after_success() -> (
    None
):
    policy = SimpleNamespace(capability="supported", min_affinity_score=None)
    hmc = cast(HMCClient, SimpleNamespace(config=object()))
    with (
        patch(
            "hmc_mcp.operations_ssh_network.get_lpar_memopt_score",
            new=AsyncMock(return_value={"curr_lpar_score": "82"}),
        ) as current,
        patch(
            "hmc_mcp.operations_ssh_network.plan_lpar_memopt_scores",
            new=AsyncMock(
                return_value=[{"lpar_name": "lpar-1", "predicted_lpar_score": "84"}]
            ),
        ) as predicted,
        patch(
            "hmc_mcp.operations_ssh_network.get_minimum_affinity_policy",
            new=AsyncMock(return_value=policy),
        ) as policy_read,
    ):
        result = await assess_post_activation_affinity(hmc, _request())

    assert result["assessment"]["classification"] == "none"
    current.assert_awaited_once()
    predicted.assert_awaited_once()
    policy_read.assert_awaited_once()


@pytest.mark.asyncio
async def test_malformed_measured_score_is_a_validation_failure() -> None:
    policy = SimpleNamespace(capability="supported", min_affinity_score=None)
    hmc = cast(HMCClient, SimpleNamespace(config=object()))
    with (
        patch(
            "hmc_mcp.operations_ssh_network.get_lpar_memopt_score",
            new=AsyncMock(return_value={"curr_lpar_score": "101"}),
        ),
        patch(
            "hmc_mcp.operations_ssh_network.plan_lpar_memopt_scores",
            new=AsyncMock(
                return_value=[{"lpar_name": "lpar-1", "predicted_lpar_score": "84"}]
            ),
        ),
        patch(
            "hmc_mcp.operations_ssh_network.get_minimum_affinity_policy",
            new=AsyncMock(return_value=policy),
        ),
        pytest.raises(ValueError, match="current_score"),
    ):
        await assess_post_activation_affinity(hmc, _request())
