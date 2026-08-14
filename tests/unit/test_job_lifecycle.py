"""Tests for shared submitted-job lifecycle handling."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hmc_mcp.jobs import job_identifier, wait_for_submitted_job


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        ({"UUID": "top"}, "top"),
        ({"Resource": {"JobID": "nested"}}, "nested"),
        ({"UUID": 42}, None),
        ({"Resource": {"JobID": ""}}, None),
        ({}, None),
    ],
)
def test_job_identifier_accepts_only_nonempty_strings(job, expected) -> None:
    assert job_identifier(job) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("wait,job", [(False, {"UUID": "job-1"}), (True, None)])
async def test_wait_for_submitted_job_returns_without_polling(wait, job) -> None:
    client = AsyncMock()

    assert await wait_for_submitted_job(client, job, wait, 30, 2) is job
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
async def test_wait_for_submitted_job_preserves_malformed_response() -> None:
    client = AsyncMock()
    job = {"UUID": 42}

    assert await wait_for_submitted_job(client, job, True, 30, 2) is job
    client.wait_for_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_wait_for_submitted_job_propagates_poll_error() -> None:
    client = AsyncMock()
    client.wait_for_job.side_effect = TimeoutError("timed out")

    with pytest.raises(TimeoutError, match="timed out"):
        await wait_for_submitted_job(client, {"UUID": "job-3"}, True, 30, 2)
