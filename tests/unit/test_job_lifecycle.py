"""Tests for shared submitted-job lifecycle handling."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pydantic import TypeAdapter

from hmc_mcp.errors import HMCError
from hmc_mcp.jobs import (
    RepositorySource,
    job_identifier,
    job_outcome,
    validate_wait_timing,
    wait_for_submitted_job,
)


def test_repository_source_builds_a_pydantic_type_adapter() -> None:
    schema = TypeAdapter(RepositorySource).json_schema()

    assert set(schema["properties"]) == set(RepositorySource.__annotations__)


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        ({"UUID": "top"}, "top"),
        ({"Resource": {"JobID": "nested"}}, "nested"),
        ({"link": "https://hmc.test/rest/api/uom/jobs/from-link"}, "from-link"),
        ({"UUID": "  trimmed  "}, "trimmed"),
        ({"UUID": 42}, None),
        ({"Resource": {"JobID": ""}}, None),
        ({}, None),
    ],
)
def test_job_identifier_accepts_only_nonempty_strings(job, expected) -> None:
    assert job_identifier(job) == expected


def test_job_identifier_skips_truthy_non_mapping_resource() -> None:
    job = {"Resource": "unexpected", "link": "/rest/api/uom/jobs/link-id"}

    assert job_identifier(job) == "link-id"


def test_job_outcome_normalizes_response_identity_and_result_error() -> None:
    job = {
        "Resource": {
            "JobID": " normalized-id ",
            "Status": "COMPLETED_WITH_ERROR",
            "Results": {
                "JobParameter": [
                    {"ParameterName": "returnCode", "ParameterValue": "1"},
                    {"ParameterName": "result", "ParameterValue": " failed "},
                ]
            },
        }
    }

    outcome = job_outcome("requested-id", job)

    assert outcome.job_id == "normalized-id"
    assert outcome.status == "COMPLETED_WITH_ERROR"
    assert outcome.timed_out is False
    assert outcome.error == "failed"
    assert outcome.job is job


def test_job_outcome_falls_back_to_requested_identity_and_exception() -> None:
    job = {
        "Resource": {
            "Status": "EXCEPTION",
            "ResponseException": {"Message": " exception text "},
        }
    }

    outcome = job_outcome(" requested-id ", job)

    assert outcome.job_id == "requested-id"
    assert outcome.status == "EXCEPTION"
    assert outcome.timed_out is False
    assert outcome.error == "exception text"


def test_job_outcome_prefers_exception_message_for_exception_status() -> None:
    job = {
        "Resource": {
            "Status": "EXCEPTION",
            "Results": {
                "JobParameter": {
                    "ParameterName": "ErrorData",
                    "ParameterValue": "less specific error",
                }
            },
            "ResponseException": {"Message": "exception text"},
        }
    }

    assert job_outcome("job-id", job).error == "exception text"


@pytest.mark.parametrize("names", [("result", "ErrorData"), ("ErrorData", "result")])
def test_job_outcome_prefers_error_data_regardless_of_parameter_order(names) -> None:
    values = {"result": "ordinary output", "ErrorData": "failure text"}
    job = {
        "Resource": {
            "Status": "COMPLETED_WITH_ERROR",
            "Results": {
                "JobParameter": [
                    {"ParameterName": name, "ParameterValue": values[name]}
                    for name in names
                ]
            },
        }
    }

    assert job_outcome("job-id", job).error == "failure text"


def test_job_outcome_tolerates_truthy_non_mapping_resource() -> None:
    outcome = job_outcome(" requested-id ", {"Resource": "unexpected"})

    assert outcome.job_id == "requested-id"
    assert outcome.status is None
    assert outcome.timed_out is True
    assert outcome.error is None


def test_job_outcome_does_not_report_success_result_as_error() -> None:
    job = {
        "Resource": {
            "JobID": "job-id",
            "Status": "COMPLETED_OK",
            "Results": {
                "JobParameter": {
                    "ParameterName": "result",
                    "ParameterValue": "success details",
                }
            },
        }
    }

    assert job_outcome("job-id", job).error is None


def test_job_outcome_marks_missing_entry_as_timed_out() -> None:
    outcome = job_outcome("job-id", None)

    assert outcome.job_id == "job-id"
    assert outcome.status is None
    assert outcome.timed_out is True
    assert outcome.error is None
    assert outcome.job is None


@pytest.mark.parametrize(
    ("timeout_seconds", "poll_interval", "message"),
    [
        (-1, 5, "timeout_seconds"),
        (300, -1, "poll_interval"),
        (300, 0, "poll_interval"),
    ],
)
def test_validate_wait_timing_rejects_invalid_values(
    timeout_seconds, poll_interval, message
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_wait_timing(True, timeout_seconds, poll_interval)


def test_validate_wait_timing_ignores_unused_values() -> None:
    validate_wait_timing(False, -1, -1)


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
