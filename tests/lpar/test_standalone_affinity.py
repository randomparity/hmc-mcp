"""Standalone PowerOn affinity-assessment contract tests."""

from hmc_mcp.operations_lpar import (
    LparPowerResult,
    activation_allows_assessment,
    classify_affinity_outcome,
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
