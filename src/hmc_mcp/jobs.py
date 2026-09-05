"""XML templates for HMC job requests (do/* operations).

Jobs are submitted with Content-Type: application/vnd.ibm.powervm.web+xml;
type=JobRequest via PUT and run asynchronously. Poll the submission's SELF link
for portable status handling; ``/rest/api/uom/jobs/{job_id}`` remains the
fallback for responses that omit that link.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, get_args
from urllib.parse import urlparse

from .errors import HMCError
from .jobs_requests import build_job_request

LuType = Literal["THIN", "THICK"]
DeviceType = Literal["VirtualIO_Disk", "VirtualIO_Image"]
RemoteRestartOperation = Literal["validate", "recover", "restart", "cleanup", "cancel"]
REMOTE_RESTART_OPERATIONS = frozenset(get_args(RemoteRestartOperation))
LU_TYPES = frozenset(get_args(LuType))
DEVICE_TYPES = frozenset(get_args(DeviceType))
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
    """Stable public result for waiting on an HMC job.

    ADR 0093 makes this field set a package-owned model contract under ADR 0029.
    ``job`` is the exception: it is an opaque HMC resource mapping whose keys and
    nesting are firmware-dependent and are not promised.

    The polling reading of the fields holds for outcomes returned by
    ``operations_jobs.get_job`` and ``operations_jobs.wait_for_job``: ``job_id``
    and ``job_href`` are the two persistable strings identifying the job, so a
    consumer can store them, restart, and poll again with a freshly constructed
    client; ``found`` says whether the HMC produced that job, and is the field to
    read first; and ``timed_out`` reports only that no terminal status was
    observed, so a job the HMC no longer knows about reports ``found=False``
    *and* ``timed_out=True``, while ``found=True`` with ``timed_out=True`` means
    the job is still running.

    A *submitting* operation that returns this type reports its own submission,
    not a poll, and the fields read differently there. ``job_id`` may be a
    synthetic label rather than a pollable handle (``power_lpar`` and
    ``provision_lpar`` pass ``"PowerOn"``; the LPM and decommission paths fall
    back to ``""``), ``found=False`` means "this submission returned no job
    entry" rather than "the HMC reaped it", and a fire-and-forget submission can
    pair ``found=False`` with ``timed_out=False``. Poll a handle only when it
    came from a polling operation or from the HMC's own submission response.
    """

    job_id: str
    status: str | None
    timed_out: bool
    error: str | None
    job: dict[str, Any] | None
    found: bool
    job_href: str | None


class JobWaitClient(Protocol):
    """Client capability required to wait for a submitted HMC job."""

    async def wait_for_job(
        self,
        job_id: str,
        timeout_seconds: int,
        poll_interval: int,
        *,
        job_href: str | None = None,
    ) -> dict[str, Any] | None: ...


def validate_wait_timing(wait: bool, timeout_seconds: int, poll_interval: int) -> None:
    """Reject invalid polling settings before a caller submits remote work."""
    if not wait:
        return
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be greater than or equal to 0")
    if poll_interval <= 0:
        raise ValueError("poll_interval must be greater than 0")


def job_identifier(job: dict[str, Any]) -> str | None:
    """Return a polling identifier from a UUID, JobID, or SELF link."""
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
    """Return the SELF link a caller can persist to poll this job again."""
    link = (job or {}).get("link")
    return link.strip() if isinstance(link, str) and link.strip() else None


def job_outcome(requested_id: str, job: dict[str, Any] | None) -> JobOutcome:
    """Normalize the last polled entry into the public wait result.

    A ``job`` of ``None`` means the HMC produced no entry for the identifier, so
    the outcome reports ``found=False``.
    """
    resource_value = (job or {}).get("Resource")
    resource = resource_value if isinstance(resource_value, dict) else {}
    status_value = resource.get("Status")
    status = status_value.strip() if isinstance(status_value, str) else None
    error = None
    if isinstance(status, str) and status in FAILED_JOB_STATUSES:
        error = _job_error(resource, status) or f"Job ended with status {status}"
    return JobOutcome(
        job_id=(job_identifier(job) if job is not None else None)
        or requested_id.strip(),
        status=status,
        timed_out=status not in TERMINAL_JOB_STATUSES,
        error=error,
        job=job,
        found=job is not None,
        job_href=_job_href(job),
    )

def _job_error(resource: dict[str, Any], status: str) -> str | None:
    """Extract the HMC result or response-exception message from a job resource."""
    exception_message = _exception_message(resource)
    if status == "EXCEPTION" and exception_message:
        return exception_message
    result_message = _result_message(resource)
    return result_message or exception_message


def _exception_message(resource: dict[str, Any]) -> str | None:
    exception = resource.get("ResponseException")
    message = exception.get("Message") if isinstance(exception, dict) else None
    return message.strip() if isinstance(message, str) and message.strip() else None


def _result_message(resource: dict[str, Any]) -> str | None:
    results = resource.get("Results")
    if not isinstance(results, dict):
        return None
    parameters = results.get("JobParameter", [])
    if isinstance(parameters, dict):
        parameters = [parameters]
    if not isinstance(parameters, list):
        return None
    messages: dict[str, str] = {}
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        name = parameter.get("ParameterName")
        value = parameter.get("ParameterValue")
        if (
            name in {"result", "detailedStatus", "ErrorData"}
            and name not in messages
            and isinstance(value, str)
            and value.strip()
        ):
            messages[name] = value.strip()
    return next(
        (messages[name] for name in ("ErrorData", "detailedStatus", "result") if name in messages),
        None,
    )


def vios_stdout(job: dict[str, Any] | None) -> str | None:
    """Return the first usable ``stdOut`` value from a VIOS job result."""
    resource = (job or {}).get("Resource")
    if not isinstance(resource, dict):
        return None
    results = resource.get("Results")
    if not isinstance(results, dict):
        return None
    parameters = results.get("JobParameter", [])
    if isinstance(parameters, dict):
        parameters = [parameters]
    if not isinstance(parameters, list):
        return None
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        value = parameter.get("ParameterValue")
        if parameter.get("ParameterName") == "stdOut" and isinstance(value, str):
            value = value.strip()
            if value:
                return value
    return None


async def wait_for_submitted_job(
    client: JobWaitClient,
    job: dict[str, Any] | None,
    wait: bool,
    timeout_seconds: int,
    poll_interval: int,
) -> dict[str, Any] | None:
    """Return immediately or honor the caller's request to poll the job."""
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
            "Cannot wait for the submitted HMC job: the response contained no usable "
            "UUID, JobID, or polling link"
        )
    return await client.wait_for_job(
        identifier, timeout_seconds, poll_interval, job_href=_job_href(job)
    )


def validate_logical_unit_types(
    lu_type: LuType, device_type: DeviceType
) -> tuple[LuType, DeviceType]:
    """Validate logical-unit serialization vocabularies for direct callers."""
    if lu_type not in LU_TYPES:
        raise ValueError(
            f"Invalid lu_type {lu_type!r}. Must be one of: {', '.join(sorted(LU_TYPES))}"
        )
    if device_type not in DEVICE_TYPES:
        raise ValueError(
            f"Invalid device_type {device_type!r}. "
            f"Must be one of: {', '.join(sorted(DEVICE_TYPES))}"
        )
    return lu_type, device_type


def power_on_lpar_job() -> str:
    return build_job_request(
        "PowerOn",
        "LogicalPartition",
        {
            "force": "false",
            "novsi": "true",
            "bootmode": "norm",
        },
    )


def power_off_lpar_job(immediate: bool = False) -> str:
    return build_job_request(
        "PowerOff",
        "LogicalPartition",
        {
            "immediate": "true" if immediate else "false",
            "restart": "false",
            "operation": "shutdown",
        },
    )


def power_on_system_job() -> str:
    return build_job_request("PowerOn", "ManagedSystem")


def power_off_system_job(immediate: bool = False) -> str:
    params = {}
    if immediate:
        params["immediate"] = "true"
    return build_job_request("PowerOff", "ManagedSystem", params or None)


def power_on_vios_job() -> str:
    return build_job_request("PowerOn", "VirtualIOServer")


def power_off_vios_job(immediate: bool = False) -> str:
    params = {}
    if immediate:
        params["immediate"] = "true"
    return build_job_request("PowerOff", "VirtualIOServer", params or None)


def create_logical_unit_job(
    lu_name: str,
    lu_size_gib: int,
    lu_type: LuType = "THIN",
    device_type: DeviceType = "VirtualIO_Disk",
    cloned_from: str | None = None,
) -> str:
    """CreateLogicalUnit job against a Cluster/SSP.

    lu_type is THICK or THIN; device_type is VirtualIO_Disk or VirtualIO_Image.
    cloned_from is the UDID of an LU to clone from (optional).
    """
    validate_logical_unit_types(lu_type, device_type)
    params: dict[str, str] = {
        "TierUDID": "",
        "LUName": lu_name,
        "LUSize": str(lu_size_gib),
        "LUType": lu_type,
        "DeviceType": device_type,
    }
    if cloned_from:
        params["ClonedFrom"] = cloned_from
    return build_job_request("CreateLogicalUnit", "Cluster", params)


def delete_logical_unit_job(lu_udid: str) -> str:
    """DeleteLogicalUnit job against a Cluster/SSP (by LU UDID)."""
    return build_job_request(
        "DeleteLogicalUnit", "Cluster", {"LogicalUnitUDID": lu_udid}
    )


# Live Partition Mobility (LPM)


def _lpm_params(target_system: str, extra: dict[str, str]) -> dict[str, str]:
    params = {"TargetManagedSystemName": target_system}
    params.update(extra)
    return params


def _migrate_job(
    operation: str,
    target_system: str,
    target_profile_name: str | None = None,
    destination_lpar_id: str | None = None,
    shared_proc_pool_id: str | None = None,
    wait_time: int | None = None,
) -> str:
    """Build a Migrate-family job request from the shared optional params."""
    extra: dict[str, str] = {}
    if target_profile_name:
        extra["TargetProfileName"] = target_profile_name
    if destination_lpar_id:
        extra["DestinationLparID"] = destination_lpar_id
    if shared_proc_pool_id:
        extra["SharedProcPoolID"] = shared_proc_pool_id
    if wait_time is not None:
        extra["WaitTime"] = str(wait_time)
    return build_job_request(
        operation, "LogicalPartition", _lpm_params(target_system, extra)
    )


def migrate_lpar_job(
    target_system: str,
    target_profile_name: str | None = None,
    destination_lpar_id: str | None = None,
    shared_proc_pool_id: str | None = None,
    wait_time: int | None = None,
) -> str:
    """Migrate job: move an LPAR to another managed system."""
    return _migrate_job(
        "Migrate",
        target_system,
        target_profile_name,
        destination_lpar_id,
        shared_proc_pool_id,
        wait_time,
    )


def migrate_validate_lpar_job(
    target_system: str,
    target_profile_name: str | None = None,
    destination_lpar_id: str | None = None,
    shared_proc_pool_id: str | None = None,
    wait_time: int | None = None,
) -> str:
    """MigrateValidate job: check whether a migration would succeed."""
    return _migrate_job(
        "MigrateValidate",
        target_system,
        target_profile_name,
        destination_lpar_id,
        shared_proc_pool_id,
        wait_time,
    )


def migrate_abort_lpar_job() -> str:
    """MigrateAbort job: cancel an in-progress migration."""
    return build_job_request("MigrateAbort", "LogicalPartition")


def migrate_recover_lpar_job() -> str:
    """MigrateRecover job: recover an LPAR after a failed migration."""
    return build_job_request("MigrateRecover", "LogicalPartition")


def remote_restart_lpar_job(
    operation: RemoteRestartOperation,
    managed_system: str,
    logical_partition_uuid: str,
    *,
    target_managed_system: str | None = None,
    target_managed_system_uuid: str | None = None,
    use_current_data: bool = False,
    retain_devices: bool = False,
) -> str:
    """Build a RemoteRestart request using its dedicated parameter vocabulary."""
    _validate_remote_restart(
        operation,
        target_managed_system,
        target_managed_system_uuid,
        use_current_data,
        retain_devices,
    )
    return build_job_request(
        "RemoteRestart",
        "LogicalPartition",
        _remote_restart_params(
            operation,
            managed_system,
            logical_partition_uuid,
            target_managed_system,
            target_managed_system_uuid,
            use_current_data,
            retain_devices,
        ),
    )


def _validate_remote_restart(
    operation: RemoteRestartOperation,
    target_managed_system: str | None,
    target_managed_system_uuid: str | None,
    use_current_data: bool,
    retain_devices: bool,
) -> None:
    if operation not in REMOTE_RESTART_OPERATIONS:
        allowed = ", ".join(sorted(REMOTE_RESTART_OPERATIONS))
        raise ValueError(f"RemoteRestart operation must be one of: {allowed}")
    if operation != "cleanup" and not (
        target_managed_system or target_managed_system_uuid
    ):
        raise ValueError(
            f"RemoteRestart {operation!r} requires a target managed system"
        )
    if target_managed_system and target_managed_system_uuid:
        raise ValueError("Specify a target managed-system name or UUID, not both")
    if use_current_data and operation != "restart":
        raise ValueError("use_current_data is valid only for RemoteRestart 'restart'")
    if retain_devices and operation != "cleanup":
        raise ValueError("retain_devices is valid only for RemoteRestart 'cleanup'")


def _remote_restart_params(
    operation: RemoteRestartOperation,
    managed_system: str,
    logical_partition_uuid: str,
    target_managed_system: str | None,
    target_managed_system_uuid: str | None,
    use_current_data: bool,
    retain_devices: bool,
) -> dict[str, str]:
    params = {
        "Operation": operation,
        "managedSystem": managed_system,
        "logicalPartitionUuid": logical_partition_uuid,
    }
    if target_managed_system:
        params["targetManagedSystem"] = target_managed_system
    if target_managed_system_uuid:
        params["targetManagedSystemUUID"] = target_managed_system_uuid
    if use_current_data:
        params["usecurrdata"] = "true"
    if retain_devices:
        params["retaindev"] = "true"
    return params


# Template Library


def deploy_partition_template_job(
    draft_template_uuid: str, target_system_uuid: str, memento: str
) -> str:
    """PartitionTemplate Deploy job.

    draft_template_uuid is the transformed template replica to deploy;
    target_system_uuid is the managed system to create the partition on; memento
    is the X-API session ID of the logged-in user.
    """
    return build_job_request(
        "Deploy",
        "PartitionTemplate",
        {
            "K_X_API_SESSION_MEMENTO": memento,
            "TargetUuid": target_system_uuid,
            "TemplateUuid": draft_template_uuid,
        },
    )
