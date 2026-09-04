"""Tests for shared submitted-job lifecycle handling."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from hmc_mcp.jobs import (
    job_identifier,
    job_outcome,
    validate_wait_timing,
    vios_stdout,
)
from hmc_mcp.operations.updates.models import (
    PlatformUpdateParameter,
    VIOSUpdateSource,
    VIOSUpgradeSource,
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


def test_platform_update_builds_a_pydantic_schema() -> None:
    schema = PlatformUpdateParameter.model_json_schema()

    assert set(schema["properties"]) == {
        "SystemFirmwareUpdate",
        "VIOSUpdate",
    }


@pytest.mark.parametrize("source", [VIOSUpdateSource, VIOSUpgradeSource])
def test_vios_source_builds_a_pydantic_type_adapter(source) -> None:
    schema = TypeAdapter(source).json_schema()

    variants = [
        schema["$defs"][entry["$ref"].rsplit("/", 1)[1]] for entry in schema["anyOf"]
    ]
    assert all("ResourceType" in variant["properties"] for variant in variants)
    assert all("ResourceType" in variant["required"] for variant in variants)


def test_vios_source_properties_are_operation_specific() -> None:
    update_schema = TypeAdapter(VIOSUpdateSource).json_schema()
    upgrade_schema = TypeAdapter(VIOSUpgradeSource).json_schema()
    update = [
        update_schema["$defs"][entry["$ref"].rsplit("/", 1)[1]]["properties"]
        for entry in update_schema["anyOf"]
    ]
    upgrade = [
        upgrade_schema["$defs"][entry["$ref"].rsplit("/", 1)[1]]["properties"]
        for entry in upgrade_schema["anyOf"]
    ]

    assert all(
        "RestartVIOS" in properties and "Disks" not in properties
        for properties in update
    )
    assert all(
        "Disks" in properties and "RestartVIOS" not in properties
        for properties in upgrade
    )


@pytest.mark.parametrize(
    ("parameters", "expected"),
    [
        ({"ParameterName": "stdOut", "ParameterValue": " log "}, "log"),
        (
            [
                None,
                {"ParameterName": "stdout", "ParameterValue": "wrong case"},
                {"ParameterName": "stdOut", "ParameterValue": 7},
                {"ParameterName": "stdOut", "ParameterValue": "  first  "},
                {"ParameterName": "stdOut", "ParameterValue": "second"},
            ],
            "first",
        ),
        ({"ParameterName": "stdOut", "ParameterValue": "   "}, None),
        ("malformed", None),
    ],
)
def test_vios_stdout_extracts_first_nonempty_string(parameters, expected) -> None:
    job = {"Resource": {"Results": {"JobParameter": parameters}}}

    assert vios_stdout(job) == expected


@pytest.mark.parametrize(
    "job",
    [None, {}, {"Resource": "bad"}, {"Resource": {"Results": "bad"}}],
)
def test_vios_stdout_ignores_malformed_job_shapes(job) -> None:
    assert vios_stdout(job) is None


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


def test_job_outcome_surfaces_detailed_status_when_error_data_is_absent() -> None:
    job = {
        "Resource": {
            "Status": "FAILED",
            "Results": {
                "JobParameter": {
                    "ParameterName": "detailedStatus",
                    "ParameterValue": "target system unavailable",
                }
            },
        }
    }

    assert job_outcome("job-id", job).error == "target system unavailable"


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
