from datetime import UTC, datetime, timedelta

import pytest

from hmc_mcp.affinity_assessment import (
    AffinityAssessmentInput,
    assess_affinity,
)


NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _input(**changes: object) -> AffinityAssessmentInput:
    values = {
        "captured_score": 90,
        "current_score": 90,
        "predicted_score": 94,
        "policy_state": "absent",
        "configured_minimum": None,
        "captured_minimum": None,
        "captured_at": NOW - timedelta(hours=1),
        "assessed_at": NOW,
        "stale_after_seconds": 7200,
        "regression_threshold": 5,
        "optimization_threshold": 5,
    }
    values.update(changes)
    return AffinityAssessmentInput(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "classification"),
    [
        (
            {
                "current_score": 79,
                "policy_state": "configured",
                "configured_minimum": 80,
                "captured_minimum": 80,
            },
            "policy-violation",
        ),
        ({"current_score": 84}, "regression"),
        ({"predicted_score": 96}, "optimization-opportunity"),
        ({}, "none"),
    ],
)
def test_classifies_supported_evidence(changes, classification) -> None:
    result = assess_affinity(_input(**changes))

    assert result.classification == classification
    assert result.evidence["captured_score"] == 90
    assert result.explanation
    assert all("apply" not in action.lower() for action in result.recommended_actions)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"current_score": None}, "current score is missing"),
        ({"policy_state": "unsupported"}, "configured policy is unsupported"),
        ({"captured_at": NOW - timedelta(hours=3)}, "captured evidence is stale"),
        (
            {"captured_at": NOW + timedelta(seconds=1)},
            "capture time is after assessment time",
        ),
        (
            {
                "policy_state": "configured",
                "configured_minimum": 80,
                "captured_minimum": 75,
            },
            "configured minimum contradicts captured policy",
        ),
        (
            {"policy_state": "absent", "captured_minimum": 80},
            "policy state contradicts captured policy",
        ),
        (
            {
                "policy_state": "configured",
                "configured_minimum": 80,
                "captured_minimum": None,
            },
            "policy state contradicts captured policy",
        ),
    ],
)
def test_returns_unsupported_data_with_evidence(changes, reason) -> None:
    result = assess_affinity(_input(**changes))

    assert result.classification == "unsupported-data"
    assert reason in result.explanation
    assert result.recommended_actions


def test_requires_caller_thresholds_without_configured_policy() -> None:
    with pytest.raises(ValueError, match="caller thresholds are required"):
        assess_affinity(_input(regression_threshold=None))


@pytest.mark.parametrize(
    "field", ["captured_score", "current_score", "predicted_score"]
)
def test_rejects_out_of_range_scores(field) -> None:
    with pytest.raises(ValueError, match="0 through 100"):
        assess_affinity(_input(**{field: 101}))


def test_prediction_is_described_as_potential_not_guaranteed() -> None:
    result = assess_affinity(_input(predicted_score=100, optimization_threshold=1))

    assert result.classification == "optimization-opportunity"
    assert "potential" in result.explanation
    assert "not guaranteed" in result.explanation
    assert "100 may be unattainable" in result.explanation


def test_none_still_warns_when_prediction_of_100_misses_caller_threshold() -> None:
    result = assess_affinity(
        _input(current_score=99, predicted_score=100, optimization_threshold=5)
    )

    assert result.classification == "none"
    assert "100 may be unattainable" in result.explanation


@pytest.mark.parametrize("value", [1.5, float("nan"), "3600"])
def test_rejects_non_integer_freshness_windows(value) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        assess_affinity(_input(stale_after_seconds=value))


def test_result_evidence_is_immutable() -> None:
    result = assess_affinity(_input())

    with pytest.raises((AttributeError, TypeError)):
        result.evidence.captured_score = 0  # type: ignore[misc]
