"""Evidence-first, read-only LPAR NUMA-affinity assessment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

AffinityClassification = Literal[
    "regression",
    "optimization-opportunity",
    "policy-violation",
    "unsupported-data",
    "none",
]
PolicyState = Literal["configured", "absent", "unsupported"]
CapturedPolicyState = Literal["configured", "absent", "unsupported", "missing"]


@dataclass(frozen=True)
class AffinityAssessmentInput:
    """Scores, policy, and freshness evidence used by one assessment."""

    captured_score: int | None
    current_score: int | None
    predicted_score: int | None
    policy_state: PolicyState
    captured_policy_state: CapturedPolicyState
    configured_minimum: int | None
    captured_minimum: int | None
    captured_at: datetime
    assessed_at: datetime
    stale_after_seconds: int
    regression_threshold: int | None = None
    optimization_threshold: int | None = None


@dataclass(frozen=True)
class AffinityEvidence:
    """Immutable normalized evidence returned with every assessment."""

    captured_score: int | None
    current_score: int | None
    predicted_score: int | None
    policy_state: PolicyState
    captured_policy_state: CapturedPolicyState
    configured_minimum: int | None
    captured_minimum: int | None
    captured_at: str
    assessed_at: str
    stale_after_seconds: int
    regression_threshold: int | None
    optimization_threshold: int | None

@dataclass(frozen=True)
class AffinityAssessmentResult:
    """Stable assessment verdict with the evidence that explains it."""

    classification: AffinityClassification
    evidence: AffinityEvidence
    explanation: str
    recommended_actions: tuple[str, ...]


def _validate_score(value: int | None, name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError(f"{name} must be an integer from 0 through 100 or null")


def _validate_threshold(value: int | None, name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer or null")


def _evidence(value: AffinityAssessmentInput) -> AffinityEvidence:
    return AffinityEvidence(
        captured_score=value.captured_score,
        current_score=value.current_score,
        predicted_score=value.predicted_score,
        policy_state=value.policy_state,
        captured_policy_state=value.captured_policy_state,
        configured_minimum=value.configured_minimum,
        captured_minimum=value.captured_minimum,
        captured_at=value.captured_at.isoformat(),
        assessed_at=value.assessed_at.isoformat(),
        stale_after_seconds=value.stale_after_seconds,
        regression_threshold=value.regression_threshold,
        optimization_threshold=value.optimization_threshold,
    )


def _unsupported(
    value: AffinityAssessmentInput, reason: str, action: str
) -> AffinityAssessmentResult:
    return AffinityAssessmentResult(
        classification="unsupported-data",
        evidence=_evidence(value),
        explanation=f"Affinity assessment is unsupported because {reason}.",
        recommended_actions=(action,),
    )


def _validate_input(value: AffinityAssessmentInput) -> None:
    """Reject malformed values and invalid caller-threshold combinations."""
    for name in (
        "captured_score",
        "current_score",
        "predicted_score",
        "configured_minimum",
        "captured_minimum",
    ):
        _validate_score(getattr(value, name), name)
    _validate_threshold(value.regression_threshold, "regression_threshold")
    _validate_threshold(value.optimization_threshold, "optimization_threshold")
    if value.policy_state not in {"configured", "absent", "unsupported"}:
        raise ValueError("policy_state must be configured, absent, or unsupported")
    if value.captured_policy_state not in {
        "configured",
        "absent",
        "unsupported",
        "missing",
    }:
        raise ValueError(
            "captured_policy_state must be configured, absent, unsupported, or missing"
        )
    if (
        isinstance(value.stale_after_seconds, bool)
        or not isinstance(value.stale_after_seconds, int)
        or value.stale_after_seconds <= 0
    ):
        raise ValueError("stale_after_seconds must be a positive integer")
    if value.captured_at.tzinfo is None or value.assessed_at.tzinfo is None:
        raise ValueError("assessment timestamps must be timezone-aware")
    if value.policy_state == "absent" and (
        value.regression_threshold is None or value.optimization_threshold is None
    ):
        raise ValueError(
            "caller thresholds are required when configured policy is absent"
        )


def _admissible_scores(
    value: AffinityAssessmentInput,
) -> tuple[int, int, int] | AffinityAssessmentResult:
    """Return normalized scores or the first reason evidence is inadmissible."""
    if value.policy_state == "unsupported":
        return _unsupported(
            value,
            "configured policy is unsupported",
            "Verify platform support, then provide caller thresholds if no policy exists.",
        )
    if value.captured_policy_state == "missing":
        return _unsupported(
            value,
            f"captured policy is {value.captured_policy_state}",
            "Capture supported policy evidence before making an operator decision.",
        )
    if value.policy_state == "absent" and (
        value.configured_minimum is not None
        or value.captured_policy_state not in {"absent", "unsupported"}
        or value.captured_minimum is not None
    ):
        return _unsupported(
            value,
            "policy state contradicts captured policy",
            "Confirm whether a minimum-affinity policy is currently configured.",
        )
    if value.policy_state == "configured" and value.configured_minimum is None:
        return _unsupported(
            value,
            "the configured minimum is missing",
            "Read the configured minimum again before making an operator decision.",
        )
    if value.policy_state == "configured" and (
        value.captured_policy_state != "configured" or value.captured_minimum is None
    ):
        return _unsupported(
            value,
            "policy state contradicts captured policy",
            "Capture a fresh policy observation before making an operator decision.",
        )
    if value.assessed_at < value.captured_at:
        return _unsupported(
            value,
            "capture time is after assessment time",
            "Correct the clock or recapture the LPAR evidence.",
        )
    age = (value.assessed_at - value.captured_at).total_seconds()
    if age > value.stale_after_seconds:
        return _unsupported(
            value,
            "captured evidence is stale",
            "Capture a fresh snapshot before comparing affinity scores.",
        )
    for name in ("captured_score", "current_score", "predicted_score"):
        if getattr(value, name) is None:
            return _unsupported(
                value,
                f"{name.replace('_', ' ')} is missing",
                "Collect all captured, current, and predicted scores, then assess again.",
            )
    if (
        value.policy_state == "configured"
        and value.captured_minimum is not None
        and value.captured_minimum != value.configured_minimum
    ):
        return _unsupported(
            value,
            "configured minimum contradicts captured policy",
            "Confirm which policy is current before acting on the assessment.",
        )

    assert value.captured_score is not None
    assert value.current_score is not None
    assert value.predicted_score is not None
    return value.captured_score, value.current_score, value.predicted_score


def assess_affinity(value: AffinityAssessmentInput) -> AffinityAssessmentResult:
    """Classify valid, admissible affinity evidence without changing the HMC."""
    _validate_input(value)
    scores = _admissible_scores(value)
    if isinstance(scores, AffinityAssessmentResult):
        return scores
    captured, current, predicted = scores
    evidence = _evidence(value)
    prediction_caveat = (
        " IBM guidance warns that 100 may be unattainable." if predicted == 100 else ""
    )
    if value.configured_minimum is not None and current < value.configured_minimum:
        return AffinityAssessmentResult(
            "policy-violation",
            evidence,
            f"Current score {current} is below configured minimum {value.configured_minimum}."
            f"{prediction_caveat}",
            (
                "Review the configured policy and investigate placement before changing the LPAR.",
            ),
        )
    if (
        value.regression_threshold is not None
        and captured - current >= value.regression_threshold
        and captured > current
    ):
        return AffinityAssessmentResult(
            "regression",
            evidence,
            f"Current score {current} regressed from captured score {captured} by {captured - current}."
            f"{prediction_caveat}",
            (
                "Investigate placement changes since capture before choosing a remediation.",
            ),
        )
    if (
        value.optimization_threshold is not None
        and predicted - current >= value.optimization_threshold
        and predicted > current
    ):
        return AffinityAssessmentResult(
            "optimization-opportunity",
            evidence,
            f"Predicted score {predicted} offers a potential gain of {predicted - current}; "
            "it is not guaranteed, and 100 may be unattainable.",
            (
                "Review the read-only prediction and document whether further operator analysis is worthwhile.",
            ),
        )
    return AffinityAssessmentResult(
        "none",
        evidence,
        "Current evidence crosses no configured or caller-supplied decision boundary; "
        "the predicted score remains potential rather than guaranteed."
        f"{prediction_caveat}",
        ("Continue monitoring and reassess when newer evidence is available.",),
    )
