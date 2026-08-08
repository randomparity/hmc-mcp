"""XML templates for HMC job requests (do/* operations).

Jobs are submitted with Content-Type: application/vnd.ibm.powervm.uom+xml;
type=JobRequest and run asynchronously; poll /rest/api/uom/Job/{uuid} for
status.
"""

from __future__ import annotations

UOM_NS = "http://www.ibm.com/xmlns/systems/power/firmware/uom/mc/2012_10/"

_JOB_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<JobRequest xmlns="{ns}" xmlns:JobRequest="{ns}" schemaVersion="V1_0">
  <Metadata>
    <Atom/>
  </Metadata>
  <RequestedOperation kb="CUR" kxe="false" schemaVersion="V1_0">
    <Metadata>
      <Atom/>
    </Metadata>
    <OperationName kb="CUR" kxe="false">{operation}</OperationName>
    <GroupName kb="CUR" kxe="false">{group}</GroupName>
  </RequestedOperation>
  <JobParameters kb="CUR" kxe="false" schemaVersion="V1_0">
    <Metadata>
      <Atom/>
    </Metadata>
{parameters}
  </JobParameters>
</JobRequest>
"""

_PARAM_TEMPLATE = """    <JobParameter kb="CUR" kxe="false" schemaVersion="V1_0">
      <Metadata>
        <Atom/>
      </Metadata>
      <ParameterName kb="CUR" kxe="false">{name}</ParameterName>
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
        ns=UOM_NS, operation=operation, group=group, parameters=params_xml
    )


def power_on_lpar_job() -> str:
    return build_job_request("PowerOn", "LogicalPartition")


def power_off_lpar_job(immediate: bool = False) -> str:
    params = {}
    if immediate:
        params["immediate"] = "true"
    return build_job_request("PowerOff", "LogicalPartition", params or None)


def power_on_system_job() -> str:
    return build_job_request("PowerOn", "ManagedSystem")


def power_off_system_job() -> str:
    return build_job_request("PowerOff", "ManagedSystem")


def create_logical_unit_job(
    lu_name: str,
    lu_size_gb: int,
    lu_type: str = "THIN",
    device_type: str = "VirtualIO_Disk",
    cloned_from: str | None = None,
) -> str:
    """CreateLogicalUnit job against a Cluster/SSP.

    lu_type is THICK or THIN; device_type is VirtualIO_Disk or VirtualIO_Image.
    cloned_from is the UDID of an LU to clone from (optional).
    """
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


def migrate_lpar_job(
    target_system: str,
    target_profile_name: str | None = None,
    destination_lpar_id: str | None = None,
    shared_proc_pool_id: str | None = None,
    wait_time: int | None = None,
) -> str:
    """Migrate job: move an LPAR to another managed system."""
    extra: dict[str, str] = {}
    if target_profile_name:
        extra["TargetProfileName"] = target_profile_name
    if destination_lpar_id:
        extra["DestinationLparID"] = destination_lpar_id
    if shared_proc_pool_id:
        extra["SharedProcPoolID"] = shared_proc_pool_id
    if wait_time is not None:
        extra["WaitTime"] = str(wait_time)
    return build_job_request("Migrate", "LogicalPartition", _lpm_params(target_system, extra))


def migrate_validate_lpar_job(
    target_system: str,
    target_profile_name: str | None = None,
    destination_lpar_id: str | None = None,
    shared_proc_pool_id: str | None = None,
    wait_time: int | None = None,
) -> str:
    """MigrateValidate job: check whether a migration would succeed."""
    extra: dict[str, str] = {}
    if target_profile_name:
        extra["TargetProfileName"] = target_profile_name
    if destination_lpar_id:
        extra["DestinationLparID"] = destination_lpar_id
    if shared_proc_pool_id:
        extra["SharedProcPoolID"] = shared_proc_pool_id
    if wait_time is not None:
        extra["WaitTime"] = str(wait_time)
    return build_job_request("MigrateValidate", "LogicalPartition", _lpm_params(target_system, extra))


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
