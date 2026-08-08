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
