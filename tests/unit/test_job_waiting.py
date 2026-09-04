"""Tests for submitted-job waiting and terminal status handling."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hmc_mcp.errors import HMCError
from hmc_mcp.jobs import (
    FAILED_JOB_STATUSES,
    SUCCESSFUL_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    job_outcome,
    wait_for_submitted_job,
)

_SUCCESSFUL_TERMINAL_STATUSES = {"COMPLETED", "COMPLETED_OK"}
_ACTIONABLE_TERMINAL_STATUSES = {
    "CANCELED_BEFORE_START",
    "CANCELED_WHILE_RUNNING",
    "COMPLETED_WITH_ERROR",
    "COMPLETED_WITH_WARNINGS",
    "EXCEPTION",
    "FAILED",
    "FAILED_BEFORE_COMPLETION",
    "FAILED_BEFORE_COMPLETION_RETRY",
    "FAILED_TO_START",
}

@pytest.mark.asyncio
@pytest.mark.parametrize("job", [{"UUID": "job-1"}, None])
async def test_wait_for_submitted_job_returns_without_polling(job) -> None:
    client = AsyncMock()

    assert await wait_for_submitted_job(client, job, False, 30, 2) is job
    client.wait_for_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_wait_for_submitted_job_forwards_identifier_link_and_timing() -> None:
    client = AsyncMock()
    client.wait_for_job.return_value = {"Status": "COMPLETED"}
    job = {"Resource": {"JobID": "job-2"}, "link": "/jobs/job-2"}

    result = await wait_for_submitted_job(client, job, True, 90, 3)

    assert result == {"Status": "COMPLETED"}
    client.wait_for_job.assert_awaited_once_with("job-2", 90, 3, job_href="/jobs/job-2")


@pytest.mark.asyncio
@pytest.mark.parametrize("link", [42, "  "])
async def test_wait_for_submitted_job_ignores_malformed_link(link) -> None:
    client = AsyncMock()
    client.wait_for_job.return_value = {"Status": "COMPLETED"}

    result = await wait_for_submitted_job(
        client, {"UUID": "job-2", "link": link}, True, 90, 3
    )

    assert result == {"Status": "COMPLETED"}
    client.wait_for_job.assert_awaited_once_with("job-2", 90, 3, job_href=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("job", [None, {"UUID": 42}, {}, {"link": "  "}])
async def test_wait_for_submitted_job_rejects_unpollable_response(job) -> None:
    client = AsyncMock()

    with pytest.raises(HMCError, match="Cannot wait for the submitted HMC job"):
        await wait_for_submitted_job(client, job, True, 30, 2)
    client.wait_for_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_wait_for_submitted_job_propagates_poll_error() -> None:
    client = AsyncMock()
    client.wait_for_job.side_effect = TimeoutError("timed out")

    with pytest.raises(TimeoutError, match="timed out"):
        await wait_for_submitted_job(client, {"UUID": "job-3"}, True, 30, 2)


def test_terminal_job_statuses_are_exhaustively_partitioned() -> None:
    assert SUCCESSFUL_JOB_STATUSES == _SUCCESSFUL_TERMINAL_STATUSES
    assert FAILED_JOB_STATUSES == _ACTIONABLE_TERMINAL_STATUSES
    assert SUCCESSFUL_JOB_STATUSES.isdisjoint(FAILED_JOB_STATUSES)
    assert SUCCESSFUL_JOB_STATUSES | FAILED_JOB_STATUSES == TERMINAL_JOB_STATUSES


@pytest.mark.parametrize("status", sorted(_ACTIONABLE_TERMINAL_STATUSES))
def test_job_outcome_marks_every_actionable_terminal_status_as_error(status) -> None:
    outcome = job_outcome("job-id", {"Resource": {"Status": status}})

    assert outcome.timed_out is False
    assert outcome.error == f"Job ended with status {status}"
