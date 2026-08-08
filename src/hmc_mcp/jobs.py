"""XML templates for HMC job requests (do/* operations).

Jobs are submitted with Content-Type: application/vnd.ibm.powervm.web+xml;
type=JobRequest via PUT and run asynchronously; poll /rest/api/uom/Job/{uuid}
for status.
"""

from __future__ import annotations

WEB_NS = "http://www.ibm.com/xmlns/systems/power/firmware/web/mc/2012_10/"

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
    return build_job_request("PowerOn", "LogicalPartition", {
        "force": "false",
        "novsi": "true",
        "bootmode": "norm",
    })


def power_off_lpar_job(immediate: bool = False) -> str:
    return build_job_request("PowerOff", "LogicalPartition", {
        "immediate": "true" if immediate else "false",
        "restart": "false",
        "operation": "shutdown",
    })


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


# ---------------------------------------------------------------------- #
# Template Library
# ---------------------------------------------------------------------- #


def partition_template_deploy_job(target_system_uuid: str, memento: str) -> str:
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


def _repository_params(repository: dict) -> dict[str, str]:
    """Convert a repository dict to JobParameter key/value pairs.

    Recognised keys (all optional):
        type        – repository type: nfs | sftp | disk | ibmfixcentral
        host        – NFS/SFTP server hostname or IP
        path        – NFS export path or SFTP remote path
        user        – SFTP username
        sftp_pw     – SFTP login credential
        mount_loc   – local mount point for NFS
        insecure    – 'true'/'false'; skip SSL/cert checks (IBM FixCentral)
        ibm_id      – IBM FixCentral account ID
        ibm_token   – IBM FixCentral account token
    The raw dict values are passed through; callers may include any
    parameter the HMC operation accepts.
    """
    return {str(k): str(v) for k, v in repository.items() if v is not None}


def hmc_update_job(repository: dict) -> str:
    """Build a JobRequest XML for an HMC software update (Install PTFs).

    target: ManagementConsole/{uuid}/do/Update
    """
    return build_job_request("Update", "ManagementConsole", _repository_params(repository))


def hmc_upgrade_job(repository: dict) -> str:
    """Build a JobRequest XML for an HMC software upgrade (full version upgrade).

    target: ManagementConsole/{uuid}/do/Upgrade
    """
    return build_job_request("Upgrade", "ManagementConsole", _repository_params(repository))


def vios_update_job(repository: dict) -> str:
    """Build a JobRequest XML for a VIOS update.

    target: VirtualIOServer/{uuid}/do/Update
    """
    return build_job_request("Update", "VirtualIOServer", _repository_params(repository))


def vios_upgrade_job(repository: dict) -> str:
    """Build a JobRequest XML for a VIOS upgrade.

    target: VirtualIOServer/{uuid}/do/Upgrade
    """
    return build_job_request("Upgrade", "VirtualIOServer", _repository_params(repository))


def firmware_update_job(repository: dict) -> str:
    """Build a JobRequest XML for a managed system firmware update.

    target: ManagedSystem/{uuid}/do/UpdateFirmware
    """
    return build_job_request("UpdateFirmware", "ManagedSystem", _repository_params(repository))


# ---------------------------------------------------------------------- #
# VIOS install (NIM-based)
# ---------------------------------------------------------------------- #


def vios_install_job(
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
