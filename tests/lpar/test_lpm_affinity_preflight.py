"""Affinity-aware LPM preflight contract and orchestration tests."""

from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from hmc_mcp.errors import HMCError
from hmc_mcp.operations_lpm import (
    LpmAffinityPreflightRequest,
    evaluate_lpm_affinity_preflight,
    migrate_lpar_with_affinity_preflight,
)
from hmc_mcp.server_lpm import hmc_migrate_lpar_with_affinity_preflight


class _ClientContext:
    def __init__(self, client: object) -> None:
        self.client = client

    async def __aenter__(self) -> object:
        return self.client

    async def __aexit__(self, *args: object) -> None:
        return None


def _request(**changes: object) -> LpmAffinityPreflightRequest:
    values: dict[str, object] = {
        "source_current_score": 85,
        "destination_estimated_score": 82,
        "destination_check_basis": "calculated",
        "configured_minimum": 80,
        "capability": "available",
        "capability_limits": ("Destination score is an estimate, not a guarantee.",),
        "response": "warn",
    }
    values.update(changes)
    return LpmAffinityPreflightRequest(**values)  # type: ignore[arg-type]


def test_preflight_passes_complete_supported_evidence() -> None:
    result = evaluate_lpm_affinity_preflight(_request())

    assert result.status == "passed"
    assert result.proceed is True
    assert asdict(result)["source_current_score"] == 85
    assert asdict(result)["destination_estimated_score"] == 82
    assert asdict(result)["configured_minimum"] == 80
    assert asdict(result)["capability_limits"]


@pytest.mark.parametrize(
    ("response", "status", "proceed"),
    [("warn", "warned", True), ("fail", "failed", False)],
)
def test_adverse_estimate_obeys_explicit_response(
    response: str, status: str, proceed: bool
) -> None:
    result = evaluate_lpm_affinity_preflight(
        _request(destination_estimated_score=79, response=response)
    )

    assert result.status == status
    assert result.proceed is proceed
    assert "below configured minimum" in result.reason


@pytest.mark.parametrize("missing", ["source_current_score", "destination_estimated_score"])
@pytest.mark.parametrize(
    ("response", "status", "proceed"),
    [("warn", "unavailable", True), ("fail", "failed", False)],
)
def test_missing_evidence_obeys_explicit_response(
    missing: str, response: str, status: str, proceed: bool
) -> None:
    result = evaluate_lpm_affinity_preflight(
        _request(**{missing: None}, response=response)
    )

    assert result.status == status
    assert result.proceed is proceed


@pytest.mark.parametrize(
    ("response", "status", "proceed"),
    [("warn", "unavailable", True), ("fail", "failed", False)],
)
def test_unsupported_capability_obeys_explicit_response(
    response: str, status: str, proceed: bool
) -> None:
    result = evaluate_lpm_affinity_preflight(
        _request(capability="unavailable", response=response)
    )

    assert result.status == status
    assert result.proceed is proceed
    assert "unavailable" in result.reason


@pytest.mark.parametrize(
    "changes",
    [
        {"source_current_score": 101},
        {"configured_minimum": -1},
        {"destination_check_basis": "guess"},
        {"capability": "maybe"},
        {"response": "implicit"},
    ],
)
def test_malformed_preflight_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        evaluate_lpm_affinity_preflight(_request(**changes))


@pytest.mark.asyncio
async def test_fail_closed_preflight_submits_no_hmc_work() -> None:
    hmc = AsyncMock()

    result = await migrate_lpar_with_affinity_preflight(
        hmc,
        "lpar-1",
        "target-1",
        _request(destination_estimated_score=70, response="fail"),
    )

    assert result.job is None
    assert result.preflight.proceed is False
    hmc.lpar_migrate_validate.assert_not_awaited()
    hmc.lpar_migrate.assert_not_awaited()


def test_mcp_fail_closed_surface_returns_stable_companion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hmc = AsyncMock()
    monkeypatch.setattr(
        "hmc_mcp.server_lpm.client_from_env", lambda profile: _ClientContext(hmc)
    )

    result = hmc_migrate_lpar_with_affinity_preflight(
        "lpar-1",
        "target-1",
        _request(destination_estimated_score=70, response="fail"),
    )

    assert set(asdict(result)) == {"lpar_uuid", "preflight", "job"}
    assert result.preflight.proceed is False
    assert result.job is None
    hmc.lpar_migrate_validate.assert_not_awaited()
    hmc.lpar_migrate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["warn", "fail"])
async def test_passing_preflight_composes_before_canonical_validation(
    response: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    hmc = AsyncMock()
    migrated = AsyncMock()

    async def fake_migrate(*args: object, **kwargs: object):
        order.append("validation-and-migration")
        return type("Result", (), {"lpar_uuid": "uuid-1", "job": "job"})()

    monkeypatch.setattr("hmc_mcp.operations_lpm.migrate_lpar", fake_migrate)
    result = await migrate_lpar_with_affinity_preflight(
        hmc, "lpar-1", "target-1", _request(response=response)
    )

    assert order == ["validation-and-migration"]
    assert result.preflight.status == "passed"
    assert result.job == "job"
    del migrated


@pytest.mark.asyncio
async def test_warning_proceeds_to_canonical_validation() -> None:
    hmc = AsyncMock()
    hmc.find_partition_by_name.return_value = {"UUID": "uuid-1"}
    hmc.list_managed_systems.return_value = [
        {"UUID": "system-1", "Resource": {"SystemName": "target-1"}}
    ]
    hmc.lpar_migrate_validate.return_value = {"UUID": "validate-1"}
    hmc.wait_for_job.return_value = {
        "Resource": {"JobID": "validate-1", "Status": "COMPLETED"}
    }
    hmc.lpar_migrate.return_value = {"UUID": "migrate-1"}

    result = await migrate_lpar_with_affinity_preflight(
        hmc,
        "lpar-1",
        "target-1",
        _request(destination_estimated_score=70, response="warn"),
    )

    assert result.preflight.status == "warned"
    hmc.lpar_migrate_validate.assert_awaited_once()
    hmc.lpar_migrate.assert_awaited_once()


@pytest.mark.asyncio
async def test_canonical_validation_timeout_never_submits_migration() -> None:
    hmc = AsyncMock()
    hmc.find_partition_by_name.return_value = {"UUID": "uuid-1"}
    hmc.list_managed_systems.return_value = [
        {"UUID": "system-1", "Resource": {"SystemName": "target-1"}}
    ]
    hmc.lpar_migrate_validate.return_value = {"UUID": "validate-1"}
    hmc.wait_for_job.return_value = {
        "Resource": {"JobID": "validate-1", "Status": "RUNNING"}
    }

    with pytest.raises(HMCError, match="migration was not submitted"):
        await migrate_lpar_with_affinity_preflight(
            hmc, "lpar-1", "target-1", _request(), timeout_seconds=0
        )

    hmc.lpar_migrate.assert_not_awaited()
