"""XML templates for HMC job requests (do/* operations).

Jobs are submitted with Content-Type: application/vnd.ibm.powervm.web+xml;
type=JobRequest via PUT and run asynchronously. Poll the submission's SELF link
for portable status handling; ``/rest/api/uom/Job/{uuid}`` remains a legacy
fallback for responses that omit that link.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, NotRequired, Protocol, TypedDict, get_args
from urllib.parse import urlparse

from .errors import HMCError
from .xmlutil import WEB_NS

LuType = Literal["THIN", "THICK"]
DeviceType = Literal["VirtualIO_Disk", "VirtualIO_Image"]
LU_TYPES = frozenset(get_args(LuType))
DEVICE_TYPES = frozenset(get_args(DeviceType))

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
FAILED_JOB_STATUSES = frozenset(
    {
        "COMPLETED_WITH_ERROR",
        "EXCEPTION",
        "FAILED",
        "FAILED_BEFORE_COMPLETION",
        "FAILED_BEFORE_COMPLETION_RETRY",
        "FAILED_TO_START",
    }
)


@dataclass(frozen=True)
class JobOutcome:
    """Stable public result for waiting on an HMC job."""

    job_id: str
    status: str | None
    timed_out: bool
    error: str | None
    job: dict[str, Any] | None


class JobWaitClient(Protocol):
    """Client capability required to wait for a submitted HMC job."""

    async def wait_for_job(
        self,
        job_uuid: str,
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
    identifier = job.get("UUID") or resource_id
    if isinstance(identifier, str) and identifier.strip():
        return identifier.strip()
    link = job.get("link")
    if not isinstance(link, str) or not link.strip():
        return None
    path = urlparse(link.strip()).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else None


def job_outcome(requested_id: str, job: dict[str, Any] | None) -> JobOutcome:
    """Normalize the last polled entry into the public wait result."""
    resource_value = (job or {}).get("Resource")
    resource = resource_value if isinstance(resource_value, dict) else {}
    status_value = resource.get("Status")
    status = status_value.strip() if isinstance(status_value, str) else None
    error = (
        _job_error(resource, status)
        if isinstance(status, str) and status in FAILED_JOB_STATUSES
        else None
    )
    return JobOutcome(
        job_id=(job_identifier(job) if job is not None else None)
        or requested_id.strip(),
        status=status,
        timed_out=status not in TERMINAL_JOB_STATUSES,
        error=error,
        job=job,
    )


def _job_error(resource: dict[str, Any], status: str) -> str | None:
    """Extract the HMC result or response-exception message from a job resource."""
    exception = resource.get("ResponseException")
    exception_message = (
        exception.get("Message") if isinstance(exception, dict) else None
    )
    if (
        status == "EXCEPTION"
        and isinstance(exception_message, str)
        and exception_message.strip()
    ):
        return exception_message.strip()

    results = resource.get("Results")
    if isinstance(results, dict):
        parameters = results.get("JobParameter", [])
        if isinstance(parameters, dict):
            parameters = [parameters]
        if isinstance(parameters, list):
            messages: dict[str, str] = {}
            for parameter in parameters:
                if not isinstance(parameter, dict):
                    continue
                name = parameter.get("ParameterName")
                value = parameter.get("ParameterValue")
                if (
                    name in {"result", "ErrorData"}
                    and name not in messages
                    and isinstance(value, str)
                    and value.strip()
                ):
                    messages[name] = value.strip()
            for name in ("ErrorData", "result"):
                if name in messages:
                    return messages[name]

    if isinstance(exception_message, str) and exception_message.strip():
        return exception_message.strip()
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
        identifier, timeout_seconds, poll_interval, job_href=job.get("link")
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


_JOB_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<JobRequest xmlns="{ns}" xmlns:JobRequest="{ns}" schemaVersion="V1_0">
  <Metadata>
    <Atom/>
  </Metadata>
  <RequestedOperation kb="CUR" kxe="false" schemaVersion="V1_0">
    <Metadata>
      <Atom/>
    </Metadata>
    <OperationName kb="ROR" kxe="false">{operation}</OperationName>
    <GroupName kb="ROR" kxe="false">{group}</GroupName>
    <ProgressType kb="ROR" kxe="false">DISCRETE</ProgressType>
  </RequestedOperation>
  <JobParameters kb="CUR" kxe="false" schemaVersion="V1_0">
    <Metadata>
      <Atom/>
    </Metadata>
{parameters}
  </JobParameters>
</JobRequest>
"""

_PARAM_TEMPLATE = """    <JobParameter schemaVersion="V1_0">
      <Metadata>
        <Atom/>
      </Metadata>
      <ParameterName kb="ROR" kxe="false">{name}</ParameterName>
      <ParameterValue kb="CUR" kxe="false">{value}</ParameterValue>
    </JobParameter>"""


def build_job_request(
    operation: str,
    group: str,
    parameters: dict[str, str] | None = None,
) -> str:
    """Build the JobRequest XML for a do/* operation."""
    params_xml = ""
    if parameters:
        params_xml = "\n".join(
            _PARAM_TEMPLATE.format(name=name, value=value)
            for name, value in parameters.items()
        )
    return _JOB_TEMPLATE.format(
        ns=WEB_NS, operation=operation, group=group, parameters=params_xml
    )


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
    lu_size_gb: int,
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
        "LUSize": str(lu_size_gb),
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


# ---------------------------------------------------------------------- #
# Live Partition Mobility (LPM)
# ---------------------------------------------------------------------- #


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


def remote_restart_lpar_job(target_system: str) -> str:
    """RemoteRestart job: restart a failed LPAR on another managed system."""
    return build_job_request(
        "RemoteRestart", "LogicalPartition", _lpm_params(target_system, {})
    )


# ---------------------------------------------------------------------- #
# Template Library
# ---------------------------------------------------------------------- #


def deploy_partition_template_job(target_system_uuid: str, memento: str) -> str:
    """PartitionTemplate Deploy job.

    target_system_uuid is the managed system to create the partition on;
    memento is the X-API session ID of the logged-in user.
    The draft template UUID is encoded in the URL, not as a parameter.
    """
    return build_job_request(
        "Deploy",
        "PartitionTemplate",
        {
            "K_X_API_SESSION_MEMENTO": memento,
            "TargetUuid": target_system_uuid,
        },
    )


# ---------------------------------------------------------------------- #
# Update / Upgrade (HMC, VIOS, firmware)
# ---------------------------------------------------------------------- #


RepositoryType = Literal["nfs", "sftp", "disk", "ibmfixcentral"]


class RepositorySource(TypedDict, total=False):
    """Software source for an update/upgrade job.

    Recognised keys:
        type        – repository type: nfs | sftp | disk | ibmfixcentral
        host        – NFS/SFTP server hostname or IP
        path        – NFS export path or SFTP remote path
        user        – SFTP username
        sftp_pw     – SFTP login credential
        mount_loc   – local mount point for NFS
        insecure    – 'true'/'false'; skip SSL/cert checks (IBM FixCentral)
        ibm_id      – IBM FixCentral account ID
        ibm_token   – IBM FixCentral account token
    """

    type: NotRequired[RepositoryType]
    host: str
    path: str
    user: str
    sftp_pw: str
    mount_loc: str
    insecure: str
    ibm_id: str
    ibm_token: str


_REPOSITORY_KEYS = frozenset(RepositorySource.__annotations__)

# The accepted repository types, derived from the RepositoryType Literal so the
# annotation and the runtime enforcement cannot drift.
_REPOSITORY_TYPES = frozenset(get_args(RepositoryType))

# Required keys per repository type; a missing one fails fast with a clear
# message instead of producing a job the HMC rejects at runtime.
_REQUIRED_KEYS: dict[RepositoryType, frozenset[str]] = {
    "nfs": frozenset({"host", "path"}),
    "sftp": frozenset({"host", "path"}),
    "disk": frozenset(),
    "ibmfixcentral": frozenset({"ibm_id", "ibm_token"}),
}


def _repository_params(repository: RepositorySource) -> dict[str, str]:
    """Convert a repository dict to JobParameter key/value pairs.

    Unknown keys are rejected, the repository type must be present, and
    required keys are checked per repository type, so a typo like
    ``{'type': 'nfs', 'hst': '...'}`` fails here with an actionable message
    instead of producing a job the HMC rejects at runtime.
    """
    unknown = set(repository) - _REPOSITORY_KEYS
    if unknown:
        raise ValueError(
            f"Unknown repository key(s): {', '.join(sorted(unknown))}. "
            f"Recognised keys: {', '.join(sorted(_REPOSITORY_KEYS))}."
        )
    repo_type = repository.get("type")
    expected = ", ".join(sorted(_REPOSITORY_TYPES))
    if repo_type is None:
        raise ValueError(
            f"Repository dict is missing 'type'. Expected one of: {expected}."
        )
    required = _REQUIRED_KEYS.get(repo_type)
    if required is None:
        raise ValueError(
            f"Unknown repository type {repo_type!r}. Expected one of: {expected}."
        )
    missing = required - set(repository)
    if missing:
        raise ValueError(
            f"Repository type {repo_type!r} requires key(s): "
            f"{', '.join(sorted(missing))}."
        )
    return {str(k): str(v) for k, v in repository.items() if v is not None}


def update_hmc_job(repository: RepositorySource) -> str:
    """Build a JobRequest XML for an HMC software update (Install PTFs).

    target: ManagementConsole/{uuid}/do/Update
    """
    return build_job_request(
        "Update", "ManagementConsole", _repository_params(repository)
    )


def upgrade_hmc_job(repository: RepositorySource) -> str:
    """Build a JobRequest XML for an HMC software upgrade (full version upgrade).

    target: ManagementConsole/{uuid}/do/Upgrade
    """
    return build_job_request(
        "Upgrade", "ManagementConsole", _repository_params(repository)
    )


def update_vios_job(repository: RepositorySource) -> str:
    """Build a JobRequest XML for a VIOS update.

    target: VirtualIOServer/{uuid}/do/Update
    """
    return build_job_request(
        "Update", "VirtualIOServer", _repository_params(repository)
    )


def upgrade_vios_job(repository: RepositorySource) -> str:
    """Build a JobRequest XML for a VIOS upgrade.

    target: VirtualIOServer/{uuid}/do/Upgrade
    """
    return build_job_request(
        "Upgrade", "VirtualIOServer", _repository_params(repository)
    )


def update_firmware_job(repository: RepositorySource) -> str:
    """Build a JobRequest XML for a managed system firmware update.

    target: ManagedSystem/{uuid}/do/UpdateFirmware
    """
    return build_job_request(
        "UpdateFirmware", "ManagedSystem", _repository_params(repository)
    )


# ---------------------------------------------------------------------- #
# VIOS install (NIM-based)
# ---------------------------------------------------------------------- #


def install_vios_job(
    nim_ip: str,
    nim_gateway: str,
    nim_subnetmask: str,
    vios_ip: str,
    vlan_id: str,
    timeout: int = 60,
) -> str:
    """InstallVIOS job: NIM-based VIOS installation.

    nim_ip is the NIM server IP address; nim_gateway and nim_subnetmask define
    the network for the VIOS during install; vios_ip is the IP the VIOS uses
    during the NIM install; vlan_id is the VLAN tag for the install network
    (pass "0" for untagged); timeout is the job timeout in minutes.
    """
    return build_job_request(
        "InstallVIOS",
        "VirtualIOServer",
        {
            "nim_IP": nim_ip,
            "nim_gateway": nim_gateway,
            "nim_subnetmask": nim_subnetmask,
            "vios_IP": vios_ip,
            "vlanid": vlan_id,
            "timeout": str(timeout),
        },
    )


# ---------------------------------------------------------------------- #
# LPAR install (NIM-based)
# ---------------------------------------------------------------------- #


def install_lpar_job(
    nim_ip: str,
    nim_gateway: str,
    nim_subnetmask: str,
    lpar_ip: str,
    vlan_id: str,
    timeout: int = 60,
) -> str:
    """InstallLPAR job: NIM-based LPAR OS installation.

    nim_ip is the NIM server IP address; nim_gateway and nim_subnetmask define
    the network for the LPAR during install; lpar_ip is the IP the LPAR uses
    during the NIM install; vlan_id is the VLAN tag for the install network
    (pass "0" for untagged); timeout is the job timeout in minutes.
    """
    return build_job_request(
        "InstallLPAR",
        "LogicalPartition",
        {
            "nim_IP": nim_ip,
            "nim_gateway": nim_gateway,
            "nim_subnetmask": nim_subnetmask,
            "lpar_IP": lpar_ip,
            "vlanid": vlan_id,
            "timeout": str(timeout),
        },
    )
