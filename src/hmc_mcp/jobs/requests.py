"""Builders for HMC ``JobRequest`` XML documents.

Request serialization is kept separate from job outcomes and polling helpers so
changes to a ``do/*`` payload do not require editing lifecycle code.
"""

from __future__ import annotations

from typing import Literal, get_args

from ..xmlutil import WEB_NS, escapes_string_arguments

LuType = Literal["THIN", "THICK"]
DeviceType = Literal["VirtualIO_Disk", "VirtualIO_Image"]
RemoteRestartOperation = Literal["validate", "recover", "restart", "cleanup", "cancel"]
REMOTE_RESTART_OPERATIONS = frozenset(get_args(RemoteRestartOperation))
LU_TYPES = frozenset(get_args(LuType))
DEVICE_TYPES = frozenset(get_args(DeviceType))

_JOB_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<JobRequest xmlns="{ns}" xmlns:JobRequest="{ns}" schemaVersion="V1_0">
  <Metadata><Atom/></Metadata>
  <RequestedOperation kb="CUR" kxe="false" schemaVersion="V1_0">
    <Metadata><Atom/></Metadata>
    <OperationName kb="ROR" kxe="false">{operation}</OperationName>
    <GroupName kb="ROR" kxe="false">{group}</GroupName>
    <ProgressType kb="ROR" kxe="false">DISCRETE</ProgressType>
  </RequestedOperation>
  <JobParameters kb="CUR" kxe="false" schemaVersion="V1_0">
    <Metadata><Atom/></Metadata>
{parameters}
  </JobParameters>
</JobRequest>
"""

_PARAM_TEMPLATE = """    <JobParameter schemaVersion="V1_0">
      <Metadata><Atom/></Metadata>
      <ParameterName kb="ROR" kxe="false">{name}</ParameterName>
      <ParameterValue kb="CUR" kxe="false">{value}</ParameterValue>
    </JobParameter>"""


@escapes_string_arguments
def build_job_request(
    operation: str,
    group: str,
    parameters: dict[str, str] | None = None,
) -> str:
    """Build and XML-escape a JobRequest document for a ``do/*`` operation."""
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
        {"force": "false", "novsi": "true", "bootmode": "norm"},
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
    parameters = {"immediate": "true"} if immediate else None
    return build_job_request("PowerOff", "ManagedSystem", parameters)


def power_on_vios_job() -> str:
    return build_job_request("PowerOn", "VirtualIOServer")


def power_off_vios_job(immediate: bool = False) -> str:
    parameters = {"immediate": "true"} if immediate else None
    return build_job_request("PowerOff", "VirtualIOServer", parameters)


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


def create_logical_unit_job(
    lu_name: str,
    lu_size_gib: int,
    lu_type: LuType = "THIN",
    device_type: DeviceType = "VirtualIO_Disk",
    cloned_from: str | None = None,
) -> str:
    """Build a CreateLogicalUnit request against a Cluster/SSP."""
    validate_logical_unit_types(lu_type, device_type)
    parameters: dict[str, str] = {
        "TierUDID": "",
        "LUName": lu_name,
        "LUSize": str(lu_size_gib),
        "LUType": lu_type,
        "DeviceType": device_type,
    }
    if cloned_from:
        parameters["ClonedFrom"] = cloned_from
    return build_job_request("CreateLogicalUnit", "Cluster", parameters)


def delete_logical_unit_job(lu_udid: str) -> str:
    """Build a DeleteLogicalUnit request against a Cluster/SSP."""
    return build_job_request(
        "DeleteLogicalUnit", "Cluster", {"LogicalUnitUDID": lu_udid}
    )


def _migrate_job(
    operation: str,
    target_system: str,
    target_profile_name: str | None = None,
    destination_lpar_id: str | None = None,
    shared_proc_pool_id: str | None = None,
    wait_time: int | None = None,
) -> str:
    parameters = {"TargetManagedSystemName": target_system}
    if target_profile_name:
        parameters["TargetProfileName"] = target_profile_name
    if destination_lpar_id:
        parameters["DestinationLparID"] = destination_lpar_id
    if shared_proc_pool_id:
        parameters["SharedProcPoolID"] = shared_proc_pool_id
    if wait_time is not None:
        parameters["WaitTime"] = str(wait_time)
    return build_job_request(operation, "LogicalPartition", parameters)


def migrate_lpar_job(
    target_system: str,
    target_profile_name: str | None = None,
    destination_lpar_id: str | None = None,
    shared_proc_pool_id: str | None = None,
    wait_time: int | None = None,
) -> str:
    """Build an LPAR migration request."""
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
    """Build an LPAR migration validation request."""
    return _migrate_job(
        "MigrateValidate",
        target_system,
        target_profile_name,
        destination_lpar_id,
        shared_proc_pool_id,
        wait_time,
    )


def migrate_abort_lpar_job() -> str:
    """Build an LPAR migration abort request."""
    return build_job_request("MigrateAbort", "LogicalPartition")


def migrate_recover_lpar_job() -> str:
    """Build an LPAR migration recovery request."""
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
    parameters = {
        "Operation": operation,
        "managedSystem": managed_system,
        "logicalPartitionUuid": logical_partition_uuid,
    }
    if target_managed_system:
        parameters["targetManagedSystem"] = target_managed_system
    if target_managed_system_uuid:
        parameters["targetManagedSystemUUID"] = target_managed_system_uuid
    if use_current_data:
        parameters["usecurrdata"] = "true"
    if retain_devices:
        parameters["retaindev"] = "true"
    return build_job_request("RemoteRestart", "LogicalPartition", parameters)


def _validate_remote_restart(
    operation: RemoteRestartOperation,
    target_managed_system: str | None,
    target_managed_system_uuid: str | None,
    use_current_data: bool,
    retain_devices: bool,
) -> None:
    """Validate the operation-specific RemoteRestart parameter vocabulary."""
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


def deploy_partition_template_job(
    draft_template_uuid: str, target_system_uuid: str, memento: str
) -> str:
    """Build a PartitionTemplate deployment request."""
    return build_job_request(
        "Deploy",
        "PartitionTemplate",
        {
            "K_X_API_SESSION_MEMENTO": memento,
            "TargetUuid": target_system_uuid,
            "TemplateUuid": draft_template_uuid,
        },
    )
