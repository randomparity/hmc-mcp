"""Validation-first LPM operation contract."""

from __future__ import annotations

from unittest.mock import AsyncMock, call

import pytest

from hmc_mcp.errors import HMCError
from hmc_mcp.jobs import JobOutcome
from hmc_mcp.operations_lpm import migrate_lpar


def _job(status: str, *, error: str | None = None) -> dict:
    resource: dict[str, object] = {"JobID": status.lower(), "Status": status}
    if error is not None:
        resource["Results"] = {
            "JobParameter": {"ParameterName": "ErrorData", "ParameterValue": error}
        }
    return {"UUID": status.lower(), "Resource": resource}


def _client(validation: dict, migration: dict | None = None) -> AsyncMock:
    client = AsyncMock()
    client.find_partition_by_name.return_value = {"UUID": "lpar-1"}
    client.lpar_migrate_validate.return_value = _job("RUNNING")
    client.wait_for_job.return_value = validation
    client.lpar_migrate.return_value = migration or _job("RUNNING")
    return client


@pytest.mark.parametrize(
    "status", ["COMPLETED", "COMPLETED_OK", "COMPLETED_WITH_WARNINGS"]
)
@pytest.mark.asyncio
async def test_default_waits_for_validation_then_submits_migration(status: str) -> None:
    client = _client(_job(status))

    result = await migrate_lpar(client, "lpar", "target", wait=False)

    assert isinstance(result.job, JobOutcome)
    assert result.job.status == "RUNNING"
    assert client.method_calls.index(
        call.lpar_migrate_validate("lpar-1", "target", None, wait_time=None)
    ) < client.method_calls.index(
        call.lpar_migrate("lpar-1", "target", None, wait_time=None)
    )
    client.wait_for_job.assert_awaited_once()


@pytest.mark.parametrize("status", ["FAILED", "EXCEPTION"])
@pytest.mark.asyncio
async def test_failed_validation_blocks_migration_and_surfaces_detail(
    status: str,
) -> None:
    client = _client(_job(status, error="validation detail"))

    with pytest.raises(HMCError, match=f"status={status}.*validation detail"):
        await migrate_lpar(client, "lpar", "target")

    client.lpar_migrate.assert_not_awaited()


@pytest.mark.asyncio
async def test_canceled_validation_blocks_migration() -> None:
    client = _client(_job("CANCELED_WHILE_RUNNING"))

    with pytest.raises(HMCError, match="status=CANCELED_WHILE_RUNNING"):
        await migrate_lpar(client, "lpar", "target")

    client.lpar_migrate.assert_not_awaited()


@pytest.mark.asyncio
async def test_timed_out_validation_blocks_migration() -> None:
    client = _client(_job("RUNNING"))

    with pytest.raises(HMCError, match="status=RUNNING"):
        await migrate_lpar(client, "lpar", "target")

    client.lpar_migrate.assert_not_awaited()


@pytest.mark.asyncio
async def test_validate_first_false_preserves_direct_submission() -> None:
    client = _client(_job("FAILED"))

    result = await migrate_lpar(client, "lpar", "target", validate_first=False)

    assert isinstance(result.job, JobOutcome)
    client.lpar_migrate_validate.assert_not_awaited()
    client.wait_for_job.assert_not_awaited()
    client.lpar_migrate.assert_awaited_once()


@pytest.mark.asyncio
async def test_effective_validation_timing_fails_before_resolution() -> None:
    client = _client(_job("COMPLETED"))

    with pytest.raises(ValueError, match="poll_interval"):
        await migrate_lpar(client, "lpar", "target", poll_interval=0)

    client.find_partition_by_name.assert_not_awaited()
