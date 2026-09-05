"""HMC job outcome normalization and polling helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from ..errors import HMCError

DEFAULT_JOB_TIMEOUT_SECONDS = 300
DEFAULT_JOB_POLL_INTERVAL = 5
TERMINAL_JOB_STATUSES = frozenset(
    {
        "CANCELED_BEFORE_START",
        "CANCELED_WHILE_RUNNING",
        "COMPLETED",
        "COMPLETED_OK",
        "COMPLETED_WITH_ERROR",
        "COMPLETED_WITH_WARNINGS",
        "EXCEPTION",
        "FAILED",
        "FAILED_BEFORE_COMPLETION",
        "FAILED_BEFORE_COMPLETION_RETRY",
        "FAILED_TO_START",
    }
)
SUCCESSFUL_JOB_STATUSES = frozenset({"COMPLETED", "COMPLETED_OK"})
FAILED_JOB_STATUSES = TERMINAL_JOB_STATUSES - SUCCESSFUL_JOB_STATUSES


@dataclass(frozen=True)
class JobOutcome:
    """Normalized result of polling an HMC job."""

    job_id: str
    status: str | None
    timed_out: bool
    error: str | None
    job: dict[str, Any] | None
    found: bool
    job_href: str | None


class JobWaitClient(Protocol):
    async def wait_for_job_entry(
        self,
        job_id: str,
        timeout_seconds: int,
        poll_interval: int,
        *,
        job_href: str | None = None,
    ) -> dict[str, Any] | None: ...


def validate_wait_timing(wait: bool, timeout_seconds: int, poll_interval: int) -> None:
    if not wait:
        return
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be greater than or equal to 0")
    if poll_interval <= 0:
        raise ValueError("poll_interval must be greater than 0")


def job_identifier(job: dict[str, Any]) -> str | None:
    resource = job.get("Resource")
    resource_id = resource.get("JobID") if isinstance(resource, dict) else None
    for candidate in (job.get("UUID"), resource_id):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    link = job.get("link")
    if not isinstance(link, str) or not link.strip():
        return None
    path = urlparse(link.strip()).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else None


def _job_href(job: dict[str, Any] | None) -> str | None:
    link = (job or {}).get("link")
    return link.strip() if isinstance(link, str) and link.strip() else None


def _result_message(resource: dict[str, Any]) -> str | None:
    results = resource.get("Results")
    parameters = results.get("JobParameter", []) if isinstance(results, dict) else []
    if isinstance(parameters, dict):
        parameters = [parameters]
    messages = {}
    for parameter in parameters if isinstance(parameters, list) else []:
        name = parameter.get("ParameterName") if isinstance(parameter, dict) else None
        value = parameter.get("ParameterValue") if isinstance(parameter, dict) else None
        if (
            name in {"result", "detailedStatus", "ErrorData"}
            and isinstance(value, str)
            and value.strip()
            and name not in messages
        ):
            messages[name] = value.strip()
    return next(
        (
            messages[name]
            for name in ("ErrorData", "detailedStatus", "result")
            if name in messages
        ),
        None,
    )


def job_outcome(requested_id: str, job: dict[str, Any] | None) -> JobOutcome:
    resource_value = (job or {}).get("Resource")
    resource = resource_value if isinstance(resource_value, dict) else {}
    status_value = resource.get("Status")
    status = status_value.strip() if isinstance(status_value, str) else None
    exception = resource.get("ResponseException")
    exception_message = (
        exception.get("Message") if isinstance(exception, dict) else None
    )
    exception_text = (
        exception_message.strip()
        if isinstance(exception_message, str) and exception_message.strip()
        else None
    )
    error = None
    if status in FAILED_JOB_STATUSES:
        error = (
            exception_text
            if status == "EXCEPTION" and exception_text
            else _result_message(resource)
            or exception_text
            or f"Job ended with status {status}"
        )
    return JobOutcome(
        (job_identifier(job) if job else None) or requested_id.strip(),
        status,
        status not in TERMINAL_JOB_STATUSES,
        error,
        job,
        job is not None,
        _job_href(job),
    )


def vios_stdout(job: dict[str, Any] | None) -> str | None:
    resource = (job or {}).get("Resource")
    results = resource.get("Results") if isinstance(resource, dict) else None
    parameters = results.get("JobParameter", []) if isinstance(results, dict) else []
    if isinstance(parameters, dict):
        parameters = [parameters]
    for parameter in parameters if isinstance(parameters, list) else []:
        value = parameter.get("ParameterValue") if isinstance(parameter, dict) else None
        if (
            isinstance(parameter, dict)
            and parameter.get("ParameterName") == "stdOut"
            and isinstance(value, str)
            and value.strip()
        ):
            return value.strip()
    return None


async def wait_for_submitted_job(
    client: JobWaitClient,
    job: dict[str, Any] | None,
    wait: bool,
    timeout_seconds: int,
    poll_interval: int,
) -> dict[str, Any] | None:
    if not wait:
        return job
    validate_wait_timing(wait, timeout_seconds, poll_interval)
    if job is None:
        raise HMCError(
            "Cannot wait for the submitted HMC job: the submission returned no job resource"
        )
    identifier = job_identifier(job)
    if identifier is None:
        raise HMCError(
            "Cannot wait for the submitted HMC job: the response contained no usable UUID, JobID, or polling link"
        )
    return await client.wait_for_job_entry(
        identifier, timeout_seconds, poll_interval, job_href=_job_href(job)
    )
