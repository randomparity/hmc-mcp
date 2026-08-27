"""XML templates for HMC job requests (do/* operations).

Jobs are submitted with Content-Type: application/vnd.ibm.powervm.web+xml;
type=JobRequest via PUT and run asynchronously. Poll the submission's SELF link
for portable status handling; ``/rest/api/uom/Job/{uuid}`` remains a legacy
fallback for responses that omit that link.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Annotated, Any, Literal, NotRequired, Protocol, Required, get_args
from urllib.parse import urlparse

from typing_extensions import TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import HMCError
from .xmlutil import WEB_NS, escapes_string_arguments

LuType = Literal["THIN", "THICK"]
DeviceType = Literal["VirtualIO_Disk", "VirtualIO_Image"]
RemoteRestartOperation = Literal["validate", "recover", "restart", "cleanup", "cancel"]
REMOTE_RESTART_OPERATIONS = frozenset(get_args(RemoteRestartOperation))
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
                    name in {"result", "detailedStatus", "ErrorData"}
                    and name not in messages
                    and isinstance(value, str)
                    and value.strip()
                ):
                    messages[name] = value.strip()
            for name in ("ErrorData", "detailedStatus", "result"):
                if name in messages:
                    return messages[name]

    if isinstance(exception_message, str) and exception_message.strip():
        return exception_message.strip()
    return None


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


@escapes_string_arguments
def build_job_request(
    operation: str,
    group: str,
    parameters: dict[str, str] | None = None,
) -> str:
    """Build the JobRequest XML for a do/* operation.

    Every ``*_job`` builder in this module renders through here, so this one
    decorator is the module's whole encoding boundary (ADR 0042).
    """
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
    return build_job_request("RemoteRestart", "LogicalPartition", params)


# ---------------------------------------------------------------------- #
# Template Library
# ---------------------------------------------------------------------- #


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


# ---------------------------------------------------------------------- #
# Update / Upgrade (HMC, VIOS, firmware)
# ---------------------------------------------------------------------- #


ConsoleUpdateMediaType = Literal[
    "USB", "NFS", "SFTP", "FTP", "IBMWebsite", "Disk", "VirtualMedia", "CDDVD"
]
_CONSOLE_UPDATE_MEDIA_TYPES = frozenset(get_args(ConsoleUpdateMediaType))
VIOSUpdateResourceType = Literal["HMC", "NFS", "SFTP", "USB", "IBMWebsite"]
VIOSUpgradeResourceType = Literal["HMC", "NFS", "SFTP", "USB"]
_VIOS_UPDATE_RESOURCE_TYPES = frozenset(get_args(VIOSUpdateResourceType))
_VIOS_UPGRADE_RESOURCE_TYPES = frozenset(get_args(VIOSUpgradeResourceType))


class ConsoleUpdateSource(TypedDict, total=False):
    """Documented parameters for ``UpdateManagementConsole``."""

    MediaType: Required[
        Annotated[
            ConsoleUpdateMediaType, Field(description="Location of the update image.")
        ]
    ]
    ServerHostOrIP: Annotated[str, Field(description="Remote server hostname or IP.")]
    UserName: Annotated[str, Field(description="Remote server username.")]
    Password: Annotated[str, Field(description="Remote server password.")]
    SFTPKey: Annotated[str, Field(description="SSH private key for SFTP.")]
    PassPhrase: Annotated[str, Field(description="SFTP private-key passphrase.")]
    Directory: Annotated[str, Field(description="HMC-local update-image directory.")]
    UpdateFile: Annotated[str, Field(description="Update image filename.")]
    MountLocation: Annotated[str, Field(description="NFS mount location.")]
    MountOptions: Annotated[str, Field(description="Additional NFS mount options.")]
    PTFNumber: Annotated[str, Field(description="PTF number for IBMWebsite.")]
    Device: Annotated[str, Field(description="USB, optical, or virtual-media device.")]
    RestartConsole: Annotated[
        Literal["True", "False"],
        Field(description="Restart the console after the update."),
    ]


_CONSOLE_UPDATE_KEYS = frozenset(ConsoleUpdateSource.__annotations__)

_VIOS_NAME = Annotated[str, Field(description="Name of the VIOS image.")]
_VIOS_SERVER = Annotated[str, Field(description="Remote server host or IP.")]
_VIOS_REMOTE_DIRECTORY = Annotated[str, Field(description="Remote image directory.")]
_VIOS_FILE_NAMES = Annotated[str, Field(description="Comma-separated image files.")]
_VIOS_MOUNT_LOCATION = Annotated[str, Field(description="NFS mount location.")]
_VIOS_MOUNT_OPTIONS = Annotated[str, Field(description="Additional NFS mount options.")]
_VIOS_USER = Annotated[str, Field(description="Remote SFTP user name.")]
_VIOS_PASSWORD = Annotated[str, Field(description="Remote SFTP password.")]
_VIOS_SSH_KEY = Annotated[str, Field(description="SSH private key for SFTP.")]
_VIOS_PASSPHRASE = Annotated[str, Field(description="SSH-key passphrase.")]
_VIOS_USB_DEVICE = Annotated[str, Field(description="USB device name.")]
_VIOS_DISKS = Annotated[
    str, Field(description="Comma-separated free physical volumes.")
]


class _VIOSOptionalSource(TypedDict, total=False):
    Name: _VIOS_NAME
    SaveFile: Annotated[str, Field(description="Save the remote image on the HMC.")]


class _VIOSUpdateOptional(_VIOSOptionalSource, total=False):
    RestartVIOS: Annotated[str, Field(description="Restart the VIOS after the update.")]


class VIOSUpdateHMCSource(TypedDict):
    ResourceType: Annotated[Literal["HMC"], Field(description="HMC image source.")]
    Name: _VIOS_NAME
    RestartVIOS: NotRequired[
        Annotated[str, Field(description="Restart the VIOS after the update.")]
    ]


class VIOSUpdateNFSSource(_VIOSUpdateOptional):
    ResourceType: Annotated[Literal["NFS"], Field(description="NFS image source.")]
    ServerHostOrIP: _VIOS_SERVER
    RemoteDirectory: _VIOS_REMOTE_DIRECTORY
    FileNames: NotRequired[_VIOS_FILE_NAMES]
    MountLocation: NotRequired[_VIOS_MOUNT_LOCATION]
    MountOptions: NotRequired[_VIOS_MOUNT_OPTIONS]


class VIOSUpdateSFTPSource(_VIOSUpdateOptional):
    ResourceType: Annotated[Literal["SFTP"], Field(description="SFTP image source.")]
    ServerHostOrIP: _VIOS_SERVER
    RemoteDirectory: _VIOS_REMOTE_DIRECTORY
    UserName: NotRequired[_VIOS_USER]
    Password: NotRequired[_VIOS_PASSWORD]
    SSHKey: NotRequired[_VIOS_SSH_KEY]
    PassPhrase: NotRequired[_VIOS_PASSPHRASE]
    FileNames: NotRequired[_VIOS_FILE_NAMES]


class VIOSUpdateUSBSource(_VIOSUpdateOptional):
    ResourceType: Annotated[Literal["USB"], Field(description="USB image source.")]
    USBDevice: _VIOS_USB_DEVICE


class VIOSUpdateIBMWebsiteSource(_VIOSUpdateOptional):
    ResourceType: Annotated[
        Literal["IBMWebsite"], Field(description="IBM website image source.")
    ]


VIOSUpdateSource = (
    VIOSUpdateHMCSource
    | VIOSUpdateNFSSource
    | VIOSUpdateSFTPSource
    | VIOSUpdateUSBSource
    | VIOSUpdateIBMWebsiteSource
)


class VIOSUpgradeHMCSource(TypedDict):
    ResourceType: Annotated[Literal["HMC"], Field(description="HMC image source.")]
    Name: _VIOS_NAME
    Disks: _VIOS_DISKS


class VIOSUpgradeNFSSource(_VIOSOptionalSource):
    ResourceType: Annotated[Literal["NFS"], Field(description="NFS image source.")]
    ServerHostOrIP: _VIOS_SERVER
    RemoteDirectory: _VIOS_REMOTE_DIRECTORY
    Disks: _VIOS_DISKS
    FileNames: NotRequired[_VIOS_FILE_NAMES]
    MountLocation: NotRequired[_VIOS_MOUNT_LOCATION]
    MountOptions: NotRequired[_VIOS_MOUNT_OPTIONS]


class VIOSUpgradeSFTPSource(_VIOSOptionalSource):
    ResourceType: Annotated[Literal["SFTP"], Field(description="SFTP image source.")]
    ServerHostOrIP: _VIOS_SERVER
    RemoteDirectory: _VIOS_REMOTE_DIRECTORY
    Disks: _VIOS_DISKS
    UserName: NotRequired[_VIOS_USER]
    Password: NotRequired[_VIOS_PASSWORD]
    SSHKey: NotRequired[_VIOS_SSH_KEY]
    PassPhrase: NotRequired[_VIOS_PASSPHRASE]
    FileNames: NotRequired[_VIOS_FILE_NAMES]


class VIOSUpgradeUSBSource(_VIOSOptionalSource):
    ResourceType: Annotated[Literal["USB"], Field(description="USB image source.")]
    USBDevice: _VIOS_USB_DEVICE
    Disks: _VIOS_DISKS


VIOSUpgradeSource = (
    VIOSUpgradeHMCSource
    | VIOSUpgradeNFSSource
    | VIOSUpgradeSFTPSource
    | VIOSUpgradeUSBSource
)


VIOSSource = VIOSUpdateSource | VIOSUpgradeSource
_VIOS_COMMON_KEYS = frozenset(
    {
        "Name",
        "ServerHostOrIP",
        "UserName",
        "Password",
        "SSHKey",
        "PassPhrase",
        "RemoteDirectory",
        "FileNames",
        "MountLocation",
        "MountOptions",
        "USBDevice",
        "SaveFile",
        "ResourceType",
    }
)
_VIOS_UPDATE_KEYS = _VIOS_COMMON_KEYS | {"ResourceType", "RestartVIOS"}
_VIOS_UPGRADE_KEYS = _VIOS_COMMON_KEYS | {"ResourceType", "Disks"}
_VIOS_UPDATE_REQUIRED = {
    "HMC": frozenset({"Name"}),
    "NFS": frozenset({"ServerHostOrIP", "RemoteDirectory"}),
    "SFTP": frozenset({"ServerHostOrIP", "RemoteDirectory"}),
    "USB": frozenset({"USBDevice"}),
    "IBMWebsite": frozenset(),
}
_VIOS_UPGRADE_REQUIRED = {
    "HMC": frozenset({"Name", "Disks"}),
    "NFS": frozenset({"ServerHostOrIP", "RemoteDirectory", "Disks"}),
    "SFTP": frozenset({"ServerHostOrIP", "RemoteDirectory", "Disks"}),
    "USB": frozenset({"USBDevice", "Disks"}),
}


_PLATFORM_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, strict=True)


class SriovAdapterUpdate(BaseModel):
    """One documented SR-IOV adapter update selection."""

    model_config = _PLATFORM_MODEL_CONFIG
    AdapterID: Annotated[
        str,
        Field(min_length=1, pattern=r"\S", description="SR-IOV adapter identifier."),
    ]
    SubType: Annotated[
        Literal["adapterdriver", "Adapter", "adapterdriver,adapter"],
        Field(description="Documented SR-IOV firmware update subtype."),
    ]


class SystemFirmwareUpdateModel(BaseModel):
    """System firmware and nested SR-IOV work for PlatformUpdate."""

    model_config = _PLATFORM_MODEL_CONFIG
    UpdateType: Annotated[
        Literal["Update", "Upgrade", "NoUpdate"],
        Field(description="System firmware action."),
    ]
    UpdateOrder: Annotated[int, Field(description="Platform update execution order.")]
    SRIOVAdapterUpdate: Annotated[
        list[SriovAdapterUpdate] | None,
        Field(description="SR-IOV adapters updated with the system firmware step."),
    ] = None

    @model_validator(mode="after")
    def reject_empty_sriov(self) -> "SystemFirmwareUpdateModel":
        """Reject an explicitly empty adapter selection."""
        if self.SRIOVAdapterUpdate == []:
            raise ValueError("SRIOVAdapterUpdate must contain at least one adapter")
        return self


class IOAdapterUpdateModel(BaseModel):
    """One documented VIOS-owned IO-adapter firmware update."""

    model_config = _PLATFORM_MODEL_CONFIG
    Id: Annotated[
        str,
        Field(min_length=1, pattern=r"\S", description="VIOS partition identifier."),
    ]
    Device: Annotated[
        str, Field(min_length=1, pattern=r"\S", description="IO-adapter device name.")
    ]
    Repository: Annotated[
        Literal["MOUNTPOINT", "SFTP", "USB", "IBMWebsite", "DISK", "disk"],
        Field(description="Documented IO-adapter image repository."),
    ]


class VIOSPlatformUpdate(BaseModel):
    """One VIOS update and its optional nested IO-adapter work."""

    model_config = _PLATFORM_MODEL_CONFIG
    UpdateType: Annotated[
        Literal["Update", "update", "Upgrade", "NoUpdate"],
        Field(description="VIOS update action."),
    ]
    VIOSName: Annotated[
        str, Field(min_length=1, pattern=r"\S", description="VIOS name.")
    ]
    UpdateOrder: Annotated[
        int | None, Field(description="Platform update execution order.")
    ] = None
    Name: Annotated[
        str | None,
        Field(min_length=1, pattern=r"\S", description="VIOS image name."),
    ] = None
    ResourceType: Annotated[
        Literal["HMC", "NFS", "SFTP", "USB", "IBMWebsite"] | None,
        Field(description="VIOS image source."),
    ] = None
    IOAdapterUpdate: Annotated[
        list[IOAdapterUpdateModel] | None,
        Field(description="IO adapters owned by this VIOS."),
    ] = None

    @model_validator(mode="after")
    def validate_update_shape(self) -> "VIOSPlatformUpdate":
        """Enforce conditional resource and non-empty adapter requirements."""
        if self.UpdateType != "NoUpdate" and self.ResourceType is None:
            raise ValueError(f"ResourceType is required for {self.UpdateType}")
        if self.IOAdapterUpdate == []:
            raise ValueError("IOAdapterUpdate must contain at least one adapter")
        return self


class PlatformUpdateParameter(BaseModel):
    """Strict documented parameter object for the PlatformUpdate operation."""

    model_config = _PLATFORM_MODEL_CONFIG
    SystemFirmwareUpdate: Annotated[
        SystemFirmwareUpdateModel | None,
        Field(description="System firmware and SR-IOV update selection."),
    ] = None
    VIOSUpdate: Annotated[
        list[VIOSPlatformUpdate] | None,
        Field(description="VIOS and IO-adapter update selections."),
    ] = None

    @model_validator(mode="after")
    def require_update_action(self) -> "PlatformUpdateParameter":
        """Reject requests that contain no firmware or adapter action."""
        if self.VIOSUpdate == []:
            raise ValueError("VIOSUpdate must contain at least one VIOS")
        system_action = self.SystemFirmwareUpdate is not None and (
            self.SystemFirmwareUpdate.UpdateType != "NoUpdate"
            or self.SystemFirmwareUpdate.SRIOVAdapterUpdate is not None
        )
        vios_action = any(
            update.UpdateType != "NoUpdate" or update.IOAdapterUpdate is not None
            for update in self.VIOSUpdate or []
        )
        if not system_action and not vios_action:
            raise ValueError("PlatformUpdate requires at least one update action")
        return self


SystemFirmwareUpdate = SystemFirmwareUpdateModel
IOAdapterUpdate = IOAdapterUpdateModel


def platform_update_job(parameters: PlatformUpdateParameter) -> dict[str, Any]:
    """Build the documented native JSON PlatformUpdate JobRequest."""
    return {
        "JobRequest": {
            "RequestedOperation": {
                "OperationName": "PlatformUpdate",
                "GroupName": "ManagedSystem",
            },
            "JobParameters": {
                "JobParameter": [
                    {
                        "ParameterName": "PlatformUpdateParameter",
                        "ParameterValue": parameters.model_dump(exclude_none=True),
                    }
                ]
            },
        }
    }


def update_hmc_job(source: ConsoleUpdateSource) -> str:
    """Build a documented ``UpdateManagementConsole`` request."""
    unknown = set(source) - _CONSOLE_UPDATE_KEYS
    if unknown:
        raise ValueError(
            f"Unknown console update parameter(s): {', '.join(sorted(unknown))}. "
            f"Recognised parameters: {', '.join(sorted(_CONSOLE_UPDATE_KEYS))}."
        )
    if "MediaType" not in source:
        raise ValueError("Console update source is missing required 'MediaType'.")
    return build_job_request(
        "UpdateManagementConsole",
        "ManagementConsole",
        {key: str(value) for key, value in source.items() if value is not None},
    )


def list_management_console_updates_job() -> str:
    """Build the parameterless ``ListManagementConsoleUpdates`` request."""
    return build_job_request("ListManagementConsoleUpdates", "ManagementConsole")


def _vios_params(
    source: Mapping[str, Any],
    operation: str,
    allowed_keys: frozenset[str],
    resource_types: frozenset[str],
    required_keys: Mapping[str, frozenset[str]],
) -> dict[str, str]:
    """Validate and stringify one documented VIOS job request."""
    unknown = set(source) - allowed_keys
    if unknown:
        raise ValueError(
            f"Unknown {operation} parameter(s): {', '.join(sorted(unknown))}. "
            f"Recognised parameters: {', '.join(sorted(allowed_keys))}."
        )
    resource_type = source.get("ResourceType")
    if resource_type is None:
        raise ValueError(f"{operation} source is missing required 'ResourceType'.")
    if resource_type not in resource_types:
        expected = ", ".join(sorted(resource_types))
        raise ValueError(
            f"Invalid {operation} ResourceType {resource_type!r}. "
            f"Expected one of: {expected}."
        )
    missing = {key for key in required_keys[resource_type] if source.get(key) is None}
    if missing:
        raise ValueError(
            f"{operation} ResourceType {resource_type!r} requires parameter(s): "
            f"{', '.join(sorted(missing))}."
        )
    save_file = source.get("SaveFile")
    if (
        isinstance(save_file, str)
        and save_file.lower() == "true"
        and source.get("Name") is None
    ):
        raise ValueError(f"{operation} SaveFile='true' requires parameter: Name.")
    return {key: str(value) for key, value in source.items() if value is not None}


def update_vios_job(source: VIOSUpdateSource) -> str:
    """Build a documented ``UpdateVIOS`` request."""
    params = _vios_params(
        source,
        "UpdateVIOS",
        _VIOS_UPDATE_KEYS,
        _VIOS_UPDATE_RESOURCE_TYPES,
        _VIOS_UPDATE_REQUIRED,
    )
    return build_job_request("UpdateVIOS", "VirtualIOServer", params)


def upgrade_vios_job(source: VIOSUpgradeSource) -> str:
    """Build a documented ``UpgradeVIOS`` request."""
    params = _vios_params(
        source,
        "UpgradeVIOS",
        _VIOS_UPGRADE_KEYS,
        _VIOS_UPGRADE_RESOURCE_TYPES,
        _VIOS_UPGRADE_REQUIRED,
    )
    return build_job_request("UpgradeVIOS", "VirtualIOServer", params)
